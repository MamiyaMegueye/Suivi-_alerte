"""Vérification des reçus manuels.

Quand le réseau est coupé, la caisse écrit des reçus manuels sur le cahier du
centre ; ils doivent ensuite être saisis dans le CRM. Le fichier des reçus
manuels (Centre · Date · N° reçu · Référence · Montant) est comparé aux
encaissements en espèces du CRM déjà chargés dans l'application.

Un reçu manuel est cherché parmi les reçus d'espèces non annulés du CRM de la
même référence d'abonné, saisis au plus tôt JOURS_AVANT jours avant la date du
reçu manuel et jusqu'à aujourd'hui (la saisie se fait au retour du réseau).
Chaque reçu du CRM ne sert qu'une fois.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timedelta

from openpyxl import load_workbook

from .releve import _sans_accent

JOURS_AVANT = 3
TOLERANCE = 0.5
LONGUEUR_REFERENCE = 8

RETROUVE = "MAN_RETROUVE"
MONTANT = "MAN_MONTANT"
ABSENT = "MAN_ABSENT"

LIBELLES = {
    RETROUVE: "Retrouvé au CRM",
    MONTANT: "Montant différent",
    ABSENT: "Non remonté au CRM",
}
ORDRE = (RETROUVE, MONTANT, ABSENT)


class FichierManuelsInvalide(ValueError):
    """Le fichier des reçus manuels ne peut pas être lu."""


# --------------------------------------------------------------------------- lecture du fichier
def _texte(v) -> str:
    return "" if v is None else str(v).strip()


def _reference(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, int):
        return str(v)
    return re.sub(r"\s+", "", str(v))


def _montant(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[\s  ]", "", str(v))
    if "," in s and "." in s:
        s = s.replace(".", "")
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 20000 < v < 80000:     # numéro de série Excel
        return date(1899, 12, 30) + timedelta(days=int(v))
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", str(v).strip())
    if m:
        j, mo, a = (int(x) for x in m.groups())
        if a < 100:
            a += 2000
        try:
            return date(a, mo, j)
        except ValueError:
            return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(v).strip())
    if m:
        try:
            return date(*(int(x) for x in m.groups()))
        except ValueError:
            return None
    return None


def _colonne(entete: list[str], motifs: tuple[str, ...], sauf: set[int] = frozenset()) -> int:
    for i, cellule in enumerate(entete):
        if i in sauf:
            continue
        if any(m in cellule for m in motifs):
            return i
    return -1


def _lire_entete(rang) -> dict | None:
    entete = [_sans_accent(c) if c is not None else "" for c in rang]
    num = _colonne(entete, ("N DE", "N°", "N °", "NUMERO", "N RECU", "NO RECU", "NUM RECU"))
    ref = _colonne(entete, ("REFER", "REF", "ABONN"), {num})
    mont = _colonne(entete, ("MONT",))
    if ref < 0 or mont < 0:
        return None
    return {
        "centre": _colonne(entete, ("CENTRE",)),
        "date": _colonne(entete, ("DATE", "DAT")),
        "num": num, "ref": ref, "montant": mont,
    }


def _rangs(contenu: bytes, nom_fichier: str):
    """Toutes les lignes de toutes les feuilles (ou du CSV)."""
    if nom_fichier.lower().endswith(".csv"):
        texte = None
        for codage in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                texte = contenu.decode(codage)
                break
            except UnicodeDecodeError:
                continue
        separateur = ";" if texte.count(";") > texte.count(",") else ","
        yield from csv.reader(io.StringIO(texte), delimiter=separateur)
        return
    try:
        classeur = load_workbook(io.BytesIO(contenu), read_only=True, data_only=True)
    except Exception as exc:
        raise FichierManuelsInvalide(
            f"Le fichier n'est pas un classeur Excel (.xlsx) ou un CSV lisible : {exc}") from exc
    for feuille in classeur.worksheets:
        yield None                     # changement de feuille : l'en-tête est recherché à nouveau
        yield from feuille.iter_rows(values_only=True)


def lire_fichier(contenu: bytes, nom_fichier: str) -> list[dict]:
    """Reçus manuels du fichier. Les lignes de mois, les en-têtes répétés et les
    lignes sans référence ou sans montant sont ignorées. Un centre vide reprend
    celui de la ligne précédente."""
    colonnes = None
    recus: list[dict] = []
    centre_courant = ""
    for rang in _rangs(contenu, nom_fichier):
        if rang is None:
            colonnes, centre_courant = None, ""
            continue
        rang = list(rang)
        if not any(c not in (None, "") for c in rang):
            continue
        entete = _lire_entete(rang)
        if entete is not None and _montant(rang[entete["montant"]] if entete["montant"] < len(rang) else None) is None:
            colonnes = entete
            continue
        if colonnes is None:
            continue

        def cellule(cle):
            i = colonnes[cle]
            return rang[i] if 0 <= i < len(rang) else None

        reference = _reference(cellule("ref"))
        montant = _montant(cellule("montant"))
        centre = _reference(cellule("centre")) if isinstance(cellule("centre"), (int, float)) \
            else _texte(cellule("centre"))
        if centre and not (reference and montant is not None):
            centre_courant = centre        # ligne « centre seul »
        if not reference or montant is None:
            continue
        centre = centre or centre_courant
        if centre:
            centre_courant = centre
        recus.append({
            "centre_fichier": centre,
            "date_recu": _date(cellule("date")),
            "num_recu_manuel": _reference(cellule("num")),
            "reference": reference,
            "montant": round(montant, 3),
        })
    if not recus:
        raise FichierManuelsInvalide(
            "Aucun reçu lu : le fichier doit contenir une ligne d'en-tête avec au moins les colonnes "
            "Référence et Montant (et de préférence Centre, Date, N° reçu).")
    return recus


def centre_code(centre_fichier: str, centres: dict[str, str]) -> str:
    """Code du centre : le numéro écrit dans le fichier, ou le centre dont le nom
    correspond. `centres` : code -> nom du centre."""
    texte = _texte(centre_fichier)
    if not texte:
        return ""
    chiffres = re.fullmatch(r"\D*(\d+)\D*", texte)
    if chiffres and chiffres.group(1).lstrip("0") in {c.lstrip("0") for c in centres}:
        return next(c for c in centres if c.lstrip("0") == chiffres.group(1).lstrip("0"))
    nom = _sans_accent(texte)
    for code, nom_centre in centres.items():
        if nom_centre and _sans_accent(nom_centre) == nom:
            return code
    for code, nom_centre in centres.items():
        n = _sans_accent(nom_centre or "")
        if n and (nom in n or n in nom):
            return code
    return chiffres.group(1) if chiffres else texte


def reference_valide(reference: str) -> str:
    """Vide si la référence a la forme attendue, sinon ce qui ne va pas."""
    if not reference:
        return "Référence vide"
    if not reference.isdigit():
        return "Contient des caractères non numériques"
    if len(reference) < LONGUEUR_REFERENCE:
        return f"Trop courte ({len(reference)} chiffres au lieu de {LONGUEUR_REFERENCE})"
    if len(reference) > LONGUEUR_REFERENCE:
        return f"Trop longue ({len(reference)} chiffres au lieu de {LONGUEUR_REFERENCE})"
    return ""


def centre_saisie(num_recu: str | None) -> str:
    """Centre de la caisse qui a saisi le reçu : le début de son numéro (les
    12 derniers chiffres sont l'année et le numéro d'ordre)."""
    n = _texte(num_recu)
    return n[:-12] if n.isdigit() and len(n) > 12 else ""


# --------------------------------------------------------------------------- comparaison
def _jour(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return _date(str(v)[:10]) if v else None


def _especes(crm: list[dict]) -> list[dict]:
    """Reçus d'espèces du CRM, avec leur jour et le centre de la caisse de saisie."""
    sortie = []
    for c in crm:
        if not (c.get("mode_paiement") or "").strip().upper().startswith("ESP"):
            continue
        j = _jour(c.get("date_comptable"))
        if j is None:
            continue
        sortie.append({**c, "_jour": j, "_ref": _reference(c.get("reference")),
                       "_centre": centre_saisie(c.get("num_recu"))})
    return sortie


def rapprocher(recus: list[dict], crm: list[dict]) -> list[dict]:
    """Cherche chaque reçu manuel dans les reçus d'espèces du CRM.

    1er passage : même référence et même montant ; 2e passage : même référence,
    autre montant. Parmi plusieurs candidats : d'abord la caisse du même centre,
    puis le délai de saisie le plus court."""
    especes = _especes(crm)
    tout: dict[str, list[dict]] = {}
    for c in especes:
        tout.setdefault(c["_ref"], []).append(c)
    utilises: set[int] = set()

    def candidats(r):
        d = r["date_recu"]
        return [c for c in tout.get(r["reference"], [])
                if id(c) not in utilises and (c.get("statut") or "") != "ANNULE"
                and (d is None or c["_jour"] >= d - timedelta(days=JOURS_AVANT))]

    def cle(r, c):
        delai = abs((c["_jour"] - r["date_recu"]).days) if r["date_recu"] else 0
        meme_centre = 0 if r["centre"] and c["_centre"] == r["centre"] else 1
        return (meme_centre, delai, c.get("num_recu") or "")

    ordre = sorted(range(len(recus)), key=lambda i: (recus[i]["date_recu"] or date.max, i))
    trouve: dict[int, dict] = {}
    for i in ordre:
        r = recus[i]
        exacts = [c for c in candidats(r) if abs(c["montant"] - r["montant"]) <= TOLERANCE]
        if exacts:
            c = min(exacts, key=lambda c: cle(r, c))
            utilises.add(id(c))
            trouve[i] = c
    proches: dict[int, dict] = {}
    for i in ordre:
        if i in trouve:
            continue
        r = recus[i]
        autres = candidats(r)
        if autres:
            c = min(autres, key=lambda c: cle(r, c))
            utilises.add(id(c))
            proches[i] = c

    lignes = []
    for i, r in enumerate(recus):
        c = trouve.get(i) or proches.get(i)
        code = RETROUVE if i in trouve else MONTANT if c is not None else ABSENT
        transactions = sorted(tout.get(r["reference"], []), key=lambda t: (t["_jour"], t.get("num_recu") or ""))
        ligne = {
            "centre": r["centre"], "nom_centre": r.get("nom_centre"),
            "centre_fichier": r["centre_fichier"],
            "date_recu": r["date_recu"].isoformat() if r["date_recu"] else None,
            "num_recu_manuel": r["num_recu_manuel"], "reference": r["reference"],
            "montant": r["montant"],
            "resultat_code": code, "resultat": LIBELLES[code],
            "anomalie_reference": reference_valide(r["reference"]) or None,
            "num_recu_crm": None, "date_crm": None, "delai_jours": None, "montant_crm": None,
            "ecart_montant": round(-r["montant"], 3) if c is None else round(c["montant"] - r["montant"], 3),
            "centre_saisie_crm": None, "statut_crm": None, "matricule": None, "nom_agent": None,
            "nb_transactions_crm": len(transactions),
            "total_crm": round(sum(t["montant"] for t in transactions if (t.get("statut") or "") != "ANNULE"), 3),
            "transactions_crm": [
                {"date": t["_jour"].isoformat(), "centre": t["_centre"] or t.get("centre"),
                 "num_recu": t.get("num_recu"), "montant": t["montant"], "statut": t.get("statut"),
                 "matricule": t.get("matricule"), "nom_agent": t.get("nom_agent"),
                 "correspondance": t is c}
                for t in transactions
            ],
            "nom_fichier": r.get("nom_fichier"),
        }
        if c is not None:
            ligne.update({
                "num_recu_crm": c.get("num_recu"), "date_crm": c["_jour"].isoformat(),
                "delai_jours": (c["_jour"] - r["date_recu"]).days if r["date_recu"] else None,
                "montant_crm": c["montant"], "centre_saisie_crm": c["_centre"] or None,
                "statut_crm": c.get("statut"), "matricule": c.get("matricule"), "nom_agent": c.get("nom_agent"),
            })
        lignes.append(ligne)
    return lignes


def references_mal_saisies(lignes: list[dict], invalides_crm: list[dict]) -> list[dict]:
    """Références qui n'ont pas 8 chiffres, dans les reçus manuels et dans le CRM."""
    sortie = [{"source": "Reçu manuel", "centre": l["centre"] or l["centre_fichier"], "date": l["date_recu"],
               "reference": l["reference"], "num_recu": l["num_recu_manuel"], "montant": l["montant"],
               "anomalie": l["anomalie_reference"]} for l in lignes if l["anomalie_reference"]]
    for c in invalides_crm:
        sortie.append({"source": "CRM", **c, "anomalie": reference_valide(_reference(c["reference"]))})
    return sortie


def _pct(part: float, total: float) -> float | None:
    return round(100.0 * part / total, 1) + 0.0 if total else None


def resumer(lignes: list[dict], base: dict | None = None, mal_saisies: list[dict] | None = None) -> dict:
    nb = len(lignes)
    montant = sum(l["montant"] for l in lignes)
    synthese = []
    for code in ORDRE:
        choisies = [l for l in lignes if l["resultat_code"] == code]
        m = sum(l["montant"] for l in choisies)
        synthese.append({"code": code, "libelle": LIBELLES[code], "nb": len(choisies), "pct_nb": _pct(len(choisies), nb),
                         "montant": round(m, 3), "pct_montant": _pct(m, montant)})
    absents = [l for l in lignes if l["resultat_code"] == ABSENT]
    ecarts = [l for l in lignes if l["resultat_code"] == MONTANT]
    delais = [l["delai_jours"] for l in lignes if l["resultat_code"] == RETROUVE and l["delai_jours"] is not None]
    mal_saisies = mal_saisies or []
    base = base or {"nb": 0, "nb_abonnes": 0}
    return {
        "synthese": synthese,
        "totaux": {
            "nb": nb, "montant": round(montant, 3),
            "nb_retrouves": nb - len(absents) - len(ecarts),
            "nb_non_remontes": len(absents),
            "montant_non_remonte": round(sum(l["montant"] for l in absents), 3),
            "pct_non_remonte": _pct(sum(l["montant"] for l in absents), montant),
            "nb_montant_different": len(ecarts),
            "ecart_montant": round(sum(l["ecart_montant"] or 0 for l in ecarts), 3),
            "ecart_montant_absolu": round(sum(abs(l["ecart_montant"] or 0) for l in ecarts), 3),
            "nb_a_investiguer": len(absents) + len(ecarts),
            "pct_a_investiguer": _pct(len(absents) + len(ecarts), nb),
            "nb_crm": base["nb"], "nb_abonnes_crm": base["nb_abonnes"],
            "nb_references_mal_saisies": len(mal_saisies),
            "nb_mal_saisies_crm": sum(1 for m in mal_saisies if m["source"] == "CRM"),
            "nb_mal_saisies_manuels": sum(1 for m in mal_saisies if m["source"] != "CRM"),
            "delai_moyen": round(sum(delais) / len(delais), 1) if delais else None,
            "delai_max": max(delais) if delais else None,
        },
    }
