"""
Exports Excel — même mise en forme que le dashboard de suivi temps réel.

Deux gabarits, repris à l'identique de l'app 1
(backend/src/api/routers/releve.py) :

- « releve » (par défaut) : celui de `_style_export_sheet` — titre bleu
  nuit 0C2D5C centré, sous-titre en italique sur DBEAFE, en-tête en
  dégradé de bleus 1E3A8A → 60A5FA, bordures fines BFDBFE, lignes
  alternées F0F7FF, en-tête figé.
- « beige » : celui de l'export des retards (`export_retards_planif`) —
  titre 5C4033, sous-titre D4B896, en-tête A0826D, bordures C9B99A,
  lignes alternées F5EBE0, lignes en retard surlignées FDECEA.

On ajoute seulement le filtre automatique sur l'en-tête.
"""

from __future__ import annotations

import io
import math
import re
from datetime import datetime

import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# ------------------------------------------------------------------
# Gabarits (couleurs de l'app 1)
# ------------------------------------------------------------------
_RELEVE = {
    "titre_fond": "0C2D5C", "titre_taille": 14,
    "sous_fond": "DBEAFE", "sous_texte": "0F172A",
    "entete": ["1E3A8A", "1E4DA8", "2560B5", "2D73C3", "3586D0",
               "3D99DD", "45A5E5", "4DAFED", "55BAF5", "60A5FA"],
    "bordure": "BFDBFE", "texte": "0F172A", "zebre": "F0F7FF",
    "alerte": None,
}
_BEIGE = {
    "titre_fond": "5C4033", "titre_taille": 16,
    "sous_fond": "D4B896", "sous_texte": "3E2723",
    "entete": ["A0826D"],
    "bordure": "C9B99A", "texte": "3E2723", "zebre": "F5EBE0",
    "alerte": "FDECEA",
}
GABARITS = {"releve": _RELEVE, "beige": _BEIGE}

# Lignes surlignées dans le gabarit beige : (colonne, valeurs).
_LIGNES_ALERTE = ("Situation", {"EN_RETARD"})

# Colonnes centrées, comme « Etat », « Cat. conso », « Estime » dans l'app 1.
_CENTRE = {
    "Gravité", "Situation", "État", "État cycle", "Statut", "Statut saisie",
    "Consultée", "Cycle", "Comparé à", "Cloture", "Code centre",
    "N° secteur", "N°",
}

# Au-delà, le style cellule par cellule devient lent : on garde titre,
# en-tête et largeurs, sans bordures ni zébrage sur les données.
_MAX_CELLULES_STYLEES = 400_000


def slug(texte: str) -> str:
    """Nom de fichier sûr."""
    s = re.sub(r"[^\w\-]+", "_", str(texte or ""), flags=re.UNICODE)
    return s.strip("_") or "export"


def _nom_feuille(texte: str) -> str:
    """Excel : 31 caractères max, pas de : \\ / ? * [ ]"""
    s = re.sub(r"[:\\/?*\[\]]", "-", str(texte or "Données"))
    return s[:31] or "Données"


def _scinder(titre: str) -> tuple[str, str | None]:
    """« A — B — C » → titre « A », sous-titre « B — C »."""
    t = str(titre or "")
    if " — " in t:
        a, b = t.split(" — ", 1)
        return a.strip(), b.strip() or None
    return t, None


def _largeurs(df: pd.DataFrame) -> list[int]:
    out = []
    for nom in df.columns:
        contenu = 0
        if len(df):
            q = df[nom].astype(str).str.len().quantile(0.92)
            if q is not None and not pd.isna(q):
                contenu = int(q) + 2
        out.append(min(max(len(str(nom)) + 3, contenu, 9), 46))
    return out


def styler(ws, df: pd.DataFrame, titre: str, sous_titre: str | None = None,
           style: str = "releve") -> None:
    """Applique le gabarit à une feuille déjà remplie.

    Titre en ligne 1, sous-titre éventuel en ligne 2, en-tête juste
    dessous (ligne 2 ou 3), données ensuite.
    """
    g = GABARITS.get(style, _RELEVE)
    nb_col = max(len(df.columns), 1)
    nb_lig = len(df)
    ligne_entete = 3 if sous_titre else 2
    largeurs = _largeurs(df)
    largeur_totale = sum(largeurs) or 40

    def _hauteur(texte: str, taille: int, minimum: int) -> int:
        # Une cellule fusionnée n'ajuste pas sa hauteur seule.
        capacite = max(int(largeur_totale * 11 / taille), 12)
        lignes = max(1, math.ceil(len(texte) / capacite))
        return max(minimum, int(taille * 1.45 * lignes) + 10)

    # ---- Titre ----
    if nb_col > 1:
        ws.merge_cells(start_row=1, start_column=1, end_row=1,
                       end_column=nb_col)
    c = ws.cell(row=1, column=1, value=titre)
    c.font = Font(name="Calibri", size=g["titre_taille"], bold=True,
                  color="FFFFFF")
    c.fill = PatternFill("solid", fgColor=g["titre_fond"])
    c.alignment = Alignment(horizontal="center", vertical="center",
                            wrap_text=True)
    ws.row_dimensions[1].height = _hauteur(titre, g["titre_taille"], 32)

    # ---- Sous-titre ----
    if sous_titre:
        if nb_col > 1:
            ws.merge_cells(start_row=2, start_column=1, end_row=2,
                           end_column=nb_col)
        c = ws.cell(row=2, column=1, value=sous_titre)
        c.font = Font(name="Calibri", size=10, italic=True,
                      color=g["sous_texte"])
        c.fill = PatternFill("solid", fgColor=g["sous_fond"])
        c.alignment = Alignment(horizontal="center", vertical="center",
                                wrap_text=True)
        ws.row_dimensions[2].height = _hauteur(sous_titre, 10, 22)

    # ---- En-tête ----
    cote = Side(style="thin", color=g["bordure"])
    bord = Border(left=cote, right=cote, top=cote, bottom=cote)
    couleurs = g["entete"]
    police_entete = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    for j in range(1, nb_col + 1):
        h = ws.cell(row=ligne_entete, column=j)
        coul = couleurs[min(j - 1, len(couleurs) - 1)]
        h.fill = PatternFill("solid", fgColor=coul)
        h.font = police_entete
        h.alignment = Alignment(horizontal="center", vertical="center",
                                wrap_text=True)
        h.border = bord
    ws.row_dimensions[ligne_entete].height = 28 if style == "releve" else 26

    for j, larg in enumerate(largeurs, start=1):
        ws.column_dimensions[get_column_letter(j)].width = larg

    # ---- Données ----
    premiere = ligne_entete + 1
    if nb_lig and nb_lig * nb_col <= _MAX_CELLULES_STYLEES:
        police = Font(name="Calibri", size=10, color=g["texte"])
        zebre = PatternFill("solid", fgColor=g["zebre"])
        alerte = (PatternFill("solid", fgColor=g["alerte"])
                  if g["alerte"] else None)
        a_droite = Alignment(horizontal="right", vertical="center")
        au_centre = Alignment(horizontal="center", vertical="center")
        a_gauche = Alignment(horizontal="left", vertical="center")
        aligns = []
        for nom in df.columns:
            if pd.api.types.is_numeric_dtype(df[nom]) \
                    and not pd.api.types.is_bool_dtype(df[nom]) \
                    and nom not in _CENTRE:
                aligns.append(a_droite)
            elif nom in _CENTRE:
                aligns.append(au_centre)
            else:
                aligns.append(a_gauche)

        marque = None
        col_alerte, val_alerte = _LIGNES_ALERTE
        if alerte is not None and col_alerte in df.columns:
            marque = df[col_alerte].isin(val_alerte).tolist()

        for i in range(nb_lig):
            r = premiere + i
            fond = None
            if marque is not None and marque[i]:
                fond = alerte
            elif i % 2 == 1:
                fond = zebre
            for j in range(1, nb_col + 1):
                cell = ws.cell(row=r, column=j)
                cell.font = police
                cell.border = bord
                cell.alignment = aligns[j - 1]
                if fond is not None:
                    cell.fill = fond
            ws.row_dimensions[r].height = 20

    # ---- Filtre sur l'en-tête ----
    if nb_lig:
        ws.auto_filter.ref = (
            f"A{ligne_entete}:{get_column_letter(nb_col)}"
            f"{ligne_entete + nb_lig}"
        )

    ws.freeze_panes = f"A{premiere}"


def _ligne_totaux(ws, df: pd.DataFrame, ligne: int, style: str) -> None:
    """Ligne « Total » sous le tableau : somme des colonnes en m3
    (volumes N, N-2, référence, écart, volume des perdus)."""
    a_sommer = [j for j, nom in enumerate(df.columns, start=1)
                if "(m3)" in str(nom)
                and pd.api.types.is_numeric_dtype(df[nom])]
    if not a_sommer:
        return
    g = GABARITS.get(style, _RELEVE)
    gras = Font(name="Calibri", size=10, bold=True, color=g["texte"])
    haut = Border(top=Side(style="medium", color=g["titre_fond"]))
    fond = PatternFill("solid", fgColor=g["sous_fond"])
    for j in range(1, len(df.columns) + 1):
        c = ws.cell(row=ligne, column=j)
        c.font, c.border, c.fill = gras, haut, fond
    ws.cell(row=ligne, column=1, value="Total")
    for j in a_sommer:
        nom = df.columns[j - 1]
        v = pd.to_numeric(df[nom], errors="coerce").sum()
        c = ws.cell(row=ligne, column=j, value=round(float(v), 1))
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.number_format = "#,##0.0"
    ws.row_dimensions[ligne].height = 22


_RE_DT = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def _sans_heure(df: pd.DataFrame) -> pd.DataFrame:
    """Colonnes date/heure → date seule (jour, mois, année), sans l'heure."""
    if df is None or df.empty:
        return df
    out = df.copy()
    for c in out.columns:
        s = out[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            out[c] = pd.to_datetime(s, errors="coerce").dt.date
            continue
        if not (s.dtype == object or pd.api.types.is_string_dtype(s)):
            continue
        nn = s.dropna()
        if nn.empty:
            continue
        x = nn.iloc[0]
        if isinstance(x, datetime) or (isinstance(x, str) and _RE_DT.match(x)):
            def _d(v):
                if isinstance(v, datetime):
                    return v.date()
                if isinstance(v, str) and _RE_DT.match(v):
                    try:
                        return datetime.fromisoformat(v.replace(" ", "T")[:19]).date()
                    except ValueError:
                        return v
                return v
            out[c] = s.map(_d)
    return out


def _ecrire(writer, df: pd.DataFrame, feuille: str, titre: str,
            sous_titre: str | None, style: str) -> None:
    nom = _nom_feuille(feuille)
    debut = 2 if sous_titre else 1
    df = _sans_heure(df)
    df.to_excel(writer, sheet_name=nom, index=False, startrow=debut)
    styler(writer.sheets[nom], df, titre, sous_titre, style)
    # dates : format jour/mois/année, sans heure
    import datetime as _d
    for ligne in writer.sheets[nom].iter_rows(min_row=debut + 2):
        for c in ligne:
            if isinstance(c.value, (_d.datetime, _d.date)):
                c.number_format = "DD/MM/YYYY"
    if len(df):
        # en-tête en ligne debut+1, données jusqu'à debut+1+len(df)
        _ligne_totaux(writer.sheets[nom], df, debut + 2 + len(df), style)


def classeur(feuilles: list[tuple], style: str = "releve") -> bytes:
    """Construit un classeur.

    Chaque feuille : (nom_feuille, titre, dataframe) ou
    (nom_feuille, titre, dataframe, sous_titre). Sans sous-titre
    explicite, le titre est coupé au premier « — » : la première partie
    devient le titre, le reste le sous-titre.
    """
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl", date_format="DD/MM/YYYY",
                        datetime_format="DD/MM/YYYY") as writer:
        vide = True
        for f in feuilles:
            nom, titre, df = f[0], f[1], f[2]
            if df is None:
                continue
            if len(f) > 3:
                t, st = titre, f[3]
            else:
                t, st = _scinder(titre)
            _ecrire(writer, df, nom, t, st, style)
            vide = False
        if vide:
            _ecrire(
                writer,
                pd.DataFrame({"Information": ["Aucune donnée"]}),
                "Vide", "Aucune donnée pour ces critères", None, style,
            )
    return buf.getvalue()


def horodatage() -> str:
    # Date du jour seulement (pas d'heure dans les exports — 22/11/2026)
    return datetime.now().strftime("%d/%m/%Y")
