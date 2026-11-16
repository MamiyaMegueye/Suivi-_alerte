"""
OBJECTIFS DE VOTRE MODÈLE POUR PLUSIEURS MOIS (montants seulement)
==================================================================
    python objectifs_mois.py 2026-10 2026-11

Pour chaque mois demandé, le modèle de lancer.py (modele_objectifs.py) est relancé comme s'il
tournait à la fin du mois précédent : il ne voit que les données disponibles à ce moment-là.
Les calculs se font dans un dossier temporaire : l'écran Objectifs et vos sorties ne sont pas modifiés.
Résultat : tableau dans le terminal + sorties/objectifs_mois.xlsx (avec l'encaissé quand il est connu).
"""
import contextlib
import importlib
import io
import os
import shutil
import sys
import tempfile

import pandas as pd

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import parametres as C  # noqa: E402

SEUIL_ROUGE = getattr(C, "SEUIL_REALISATION_ROUGE", 60)   # réalisation < 60 % affichée en rouge
DONNEES, SORTIES = C.DOSSIER_DONNEES, C.DOSSIER_SORTIES   # dossiers d'origine (relatifs au projet)


def objectifs_du_mois(mois):
    fin = C._decaler(mois, -1)
    tmp = tempfile.mkdtemp(prefix="objectifs_")
    d_in, d_out = os.path.join(tmp, "donnees"), os.path.join(tmp, "sorties")
    os.makedirs(d_in)
    os.makedirs(d_out)
    src = os.path.join(ICI, DONNEES)
    for f in os.listdir(src):
        if not f.endswith(".csv"):
            continue
        df = pd.read_csv(os.path.join(src, f), sep=";", encoding="utf-8-sig", dtype=str)
        if "mois" in df.columns:          # seules les données connues à la fin du mois précédent
            df = df[df["mois"] <= fin]
        df.to_csv(os.path.join(d_in, f), sep=";", index=False, encoding="utf-8-sig")
    # Période du calcul
    C.MOIS_FIN = fin
    C.MOIS_DEBUT = C._decaler(fin, -(C.FENETRE_MOIS - 1))
    C.MOIS_OBJECTIF = mois
    C.MOIS_DEBUT_ENCAISSEMENTS = min(C.MOIS_DEBUT, f"{C.ANNEE_PIC}-01",
                                     C._decaler(fin, -(getattr(C, "HISTORIQUE_ENCAISSEMENTS", 12) - 1)))
    C.DOSSIER_DONNEES, C.DOSSIER_SORTIES = d_in, d_out
    import modele_objectifs as M
    M = importlib.reload(M)
    with contextlib.redirect_stdout(io.StringIO()):
        M.main()
    o = pd.read_excel(os.path.join(d_out, f"objectifs_{mois}.xlsx"), sheet_name="Objectifs")
    o["Centre"] = o["Centre"].astype(str)
    o = o[o["Centre"] != "TOTAL"]
    col = next(c for c in o.columns if str(c).startswith("OBJECTIF"))
    shutil.rmtree(tmp, ignore_errors=True)
    return o.set_index("Centre")[["Nom centre", col]].rename(columns={col: mois})


def main():
    mois_list = [a for a in sys.argv[1:] if len(a) == 7 and a[4] == "-"]
    if not mois_list:
        print(__doc__)
        return
    dossier_donnees, dossier_sorties = DONNEES, SORTIES
    res = None
    for m in mois_list:
        print(f"  calcul des objectifs de {m} ...")
        o = objectifs_du_mois(m)
        res = o if res is None else res.join(o[[m]], how="outer")
    C.DOSSIER_DONNEES, C.DOSSIER_SORTIES = dossier_donnees, dossier_sorties
    # Encaissé réel quand il est connu
    e = pd.read_csv(os.path.join(ICI, dossier_donnees, C.FICHIERS["encaissements"]), sep=";",
                    encoding="utf-8-sig", dtype={"centre": str})
    e["montant"] = pd.to_numeric(e["montant"], errors="coerce")
    enc = e.pivot_table(index="centre", columns="mois", values="montant", aggfunc="sum")
    for m in mois_list:
        if m in enc.columns:
            res[f"Encaissé {m}"] = enc[m].reindex(res.index)
            res[f"Réalisation {m} (%)"] = 100 * res[f"Encaissé {m}"] / res[m]
    res = res.sort_values(mois_list[-1], ascending=False)

    larg = 14
    entete = f"  {'Centre':<6} {'Nom centre':<24}" + "".join(f"{'Objectif ' + m:>{larg + 4}}" for m in mois_list)
    print("\n" + entete)
    for c, l in res.iterrows():
        print(f"  {c:<6} {str(l['Nom centre'])[:24]:<24}"
              + "".join(f"{l[m]:>{larg + 4},.0f}" for m in mois_list))
    print(f"  {'TOTAL':<31}" + "".join(f"{res[m].sum():>{larg + 4},.0f}" for m in mois_list))
    for m in mois_list:
        if f"Encaissé {m}" in res:
            print(f"  {m} : encaissé {res[f'Encaissé {m}'].sum():,.0f} | réalisation "
                  f"{100 * res[f'Encaissé {m}'].sum() / res[m].sum():.0f} %")

    chemin = os.path.join(ICI, dossier_sorties, "objectifs_mois.xlsx")
    exporter_par_zone(res, mois_list, chemin)
    print(f"\n  ✅ {chemin}")


def lire_zones():
    """Répartition des centres par zone (donnees/centres_par_zone.xlsx : ZONE, EXP, CENTRE)."""
    ch = os.path.join(ICI, DONNEES, getattr(C, "FICHIER_CENTRES_PAR_ZONE", "centres_par_zone.xlsx"))
    if not os.path.exists(ch):
        return None, []
    z = pd.read_excel(ch)
    z.columns = ["zone", "exp", "centre"][:len(z.columns)]
    z["exp"] = z["exp"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    z["zone"] = z["zone"].astype(str).str.strip()
    ordre = list(dict.fromkeys(z["zone"]))
    return z.set_index("exp")["zone"], ordre


def exporter_par_zone(res, mois_list, chemin):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, GradientFill, PatternFill, Side
    from openpyxl.utils import get_column_letter

    zones, ordre = lire_zones()
    res = res.copy()
    res["zone"] = res.index.map(zones) if zones is not None else "TOUS LES CENTRES"
    res["zone"] = res["zone"].fillna("À CLASSER (absents du fichier des zones)")
    if res["zone"].str.startswith("À CLASSER").any():
        ordre = ordre + ["À CLASSER (absents du fichier des zones)"]
    if zones is None:
        ordre = ["TOUS LES CENTRES"]

    # Colonnes : Exp, Centre, puis pour chaque mois objectif / encaissé / réalisation
    cols = []
    for m in mois_list:
        cols.append((f"Objectif {m}", m, "mt"))
        if f"Encaissé {m}" in res:
            cols += [(f"Encaissé {m}", f"Encaissé {m}", "mt"), (f"Réalisation {m}", None, "pct:" + m)]
    # Charte bleue des fichiers SNDE (même palette que SNDE_nouveautes_GC)
    marine, roi, ciel_vif, bleu_pale, bleu_trait, ligne_alt, texte = (
        "0C2D5C", "1E3A8A", "60A5FA", "DBEAFE", "BFDBFE", "F0F7FF", "0F172A")
    police = "Calibri"
    trait = Side(style="thin", color=bleu_trait)
    cadre = Border(left=trait, right=trait, top=trait, bottom=trait)
    blanc = Side(style="thin", color="FFFFFF")
    cadre_entete = Border(left=blanc, right=blanc, top=blanc, bottom=Side(style="medium", color=marine))

    def degrade(j, n):
        """Couleur de l'en-tête de la colonne j (1..n) : du bleu roi au bleu ciel, comme le fichier GC."""
        a, b = tuple(int(roi[i:i + 2], 16) for i in (0, 2, 4)), tuple(int(ciel_vif[i:i + 2], 16) for i in (0, 2, 4))
        t = (j - 1) / max(n - 1, 1)
        return "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(a, b))
    nb = 2 + len(cols)
    fin = get_column_letter(nb)

    wb = Workbook()
    ws = wb.active
    ws.title = "Objectifs par zone"
    ws.sheet_view.showGridLines = False

    def bandeau(ligne, texte, taille, fond, hauteur, couleur_txt="FFFFFF", gras=True, italique=False):
        ws.merge_cells(f"A{ligne}:{fin}{ligne}")
        c = ws[f"A{ligne}"]
        c.value = texte
        c.font = Font(name=police, size=taille, bold=gras, italic=italique, color=couleur_txt)
        c.fill = PatternFill("solid", fgColor=fond)
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[ligne].height = hauteur

    bandeau(1, "OBJECTIFS D'ENCAISSEMENT PAR ZONE ET PAR CENTRE — SNDE", 18, marine, 34)
    bandeau(2, "Mois : " + "  •  ".join(mois_list) + "   |   Modèle multiniveau", 10, bleu_pale, 20, texte, False)

    def ecrire_ligne(r, valeurs, style):
        for j, v in enumerate(valeurs, start=1):
            c = ws.cell(row=r, column=j, value=v)
            c.border = cadre
            kind = "txt" if j <= 2 else cols[j - 3][2]
            if style == "entete":
                c.font = Font(name=police, size=11, bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", fgColor=degrade(j, len(valeurs)))
                c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                c.border = cadre_entete
                continue
            if style == "zone_total":
                c.font = Font(name=police, size=10, bold=True, color=marine)
                c.fill = PatternFill("solid", fgColor=bleu_trait)
            elif style == "general":
                c.font = Font(name=police, size=11, bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", fgColor=roi)
            else:
                c.font = Font(name=police, size=10, color=texte)
                c.fill = PatternFill("solid", fgColor=ligne_alt if style == "pair" else "FFFFFF")
            if kind == "mt":
                c.number_format = "#,##0"
                c.alignment = Alignment(horizontal="right", vertical="center")
            elif kind.startswith("pct"):
                c.number_format = '0" %"'
                c.alignment = Alignment(horizontal="center", vertical="center")
                if isinstance(v, (int, float)) and v == v and round(v) < SEUIL_ROUGE:   # réalisation affichée < 60 %
                    c.fill = PatternFill("solid", fgColor="E53935")
                    c.font = Font(name=police, size=c.font.size, bold=True, color="FFFFFF")
            else:
                c.alignment = Alignment(horizontal="left" if j == 2 else "center", vertical="center", indent=1 if j == 2 else 0)

    r = 4
    entetes = ["Exp", "Centre"] + [c[0] for c in cols]
    ecrire_ligne(r, entetes, "entete")
    ws.row_dimensions[r].height = 30
    r += 1
    synthese = []
    tot_gen = {c[1]: 0.0 for c in cols if c[1]}
    for zn in ordre:
        sous = res[res["zone"] == zn].sort_values(mois_list[-1], ascending=False)
        if sous.empty:
            continue
        r += 1
        bandeau(r, f"  {zn}  —  {len(sous)} centre(s)", 12, bleu_pale, 24, roi)
        ws[f"A{r}"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
        r += 1
        for k, (c, l) in enumerate(sous.iterrows()):
            vals = [c, l["Nom centre"]]
            for _, src, kind in cols:
                if kind.startswith("pct"):
                    m = kind.split(":")[1]
                    vals.append(100 * l[f"Encaissé {m}"] / l[m] if l[m] else None)
                else:
                    vals.append(l[src])
            ecrire_ligne(r, vals, "pair" if k % 2 else "impair")
            ws.row_dimensions[r].height = 20
            r += 1
        tz = {src: sous[src].sum() for _, src, _ in cols if src}
        vals = ["", f"Total {zn}"]
        for _, src, kind in cols:
            if kind.startswith("pct"):
                m = kind.split(":")[1]
                vals.append(100 * tz[f"Encaissé {m}"] / tz[m] if tz[m] else None)
            else:
                vals.append(tz[src])
        ecrire_ligne(r, vals, "zone_total")
        ws.row_dimensions[r].height = 20
        r += 1
        synthese.append((zn, len(sous), tz))
        for kx in tz:
            tot_gen[kx] += tz[kx]
    r += 1
    vals = ["", "TOTAL GÉNÉRAL"]
    for _, src, kind in cols:
        if kind.startswith("pct"):
            m = kind.split(":")[1]
            vals.append(100 * tot_gen[f"Encaissé {m}"] / tot_gen[m] if tot_gen[m] else None)
        else:
            vals.append(tot_gen[src])
    ecrire_ligne(r, vals, "general")
    ws.row_dimensions[r].height = 26

    ws.column_dimensions["A"].width = 7
    ws.column_dimensions["B"].width = 30
    for j in range(3, nb + 1):
        ws.column_dimensions[get_column_letter(j)].width = 17 if cols[j - 3][2] == "mt" else 14
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "4:4"

    # --- Feuille de synthèse par zone
    s2 = wb.create_sheet("Synthèse par zone")
    s2.sheet_view.showGridLines = False
    ws_old, ws = ws, s2
    bandeau(1, "SYNTHÈSE PAR ZONE — SNDE", 18, marine, 34)
    bandeau(2, "Mois : " + "  •  ".join(mois_list), 10, bleu_pale, 20, texte, False)
    ecrire_ligne(4, ["Centres", "Zone"] + [c[0] for c in cols], "entete")
    s2.row_dimensions[4].height = 30
    rr = 5
    for k, (zn, n, tz) in enumerate(synthese):
        vals = [n, zn]
        for _, src, kind in cols:
            if kind.startswith("pct"):
                m = kind.split(":")[1]
                vals.append(100 * tz[f"Encaissé {m}"] / tz[m] if tz[m] else None)
            else:
                vals.append(tz[src])
        ecrire_ligne(rr, vals, "pair" if k % 2 else "impair")
        s2.row_dimensions[rr].height = 20
        rr += 1
    vals = [sum(n for _, n, _ in synthese), "TOTAL GÉNÉRAL"]
    for _, src, kind in cols:
        if kind.startswith("pct"):
            m = kind.split(":")[1]
            vals.append(100 * tot_gen[f"Encaissé {m}"] / tot_gen[m] if tot_gen[m] else None)
        else:
            vals.append(tot_gen[src])
    ecrire_ligne(rr, vals, "general")
    s2.row_dimensions[rr].height = 26
    s2.column_dimensions["A"].width = 10
    s2.column_dimensions["B"].width = 44
    for j in range(3, nb + 1):
        s2.column_dimensions[get_column_letter(j)].width = 17 if cols[j - 3][2] == "mt" else 14
    s2.page_setup.orientation = "landscape"
    wb.move_sheet("Synthèse par zone", offset=-1)
    wb.active = 0
    wb.save(chemin)
    a_classer = res[res["zone"].str.startswith("À CLASSER")]
    if len(a_classer):
        print("  ⚠ Centres absents du fichier des zones : "
              + ", ".join(f"{c} {n}" for c, n in a_classer["Nom centre"].items()))


if __name__ == "__main__":
    main()
