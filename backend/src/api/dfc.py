"""Croisement avec la saisie de la DFC.

Chaque centre envoie chaque jour ses chiffres à la DFC, qui les saisit dans un
classeur Excel par centre : un bloc par mois, une ligne par jour, avec les
encaissements (espèces, chèques, virements), les versements répartis par
banque, les dépenses et les remboursements. Le fichier porte le numéro du
centre dans son nom (« 51.xlsx »).

Ce module lit ce classeur et compare chaque jour saisi avec le CRM.
"""
import io
import re
from datetime import date, datetime

from openpyxl import load_workbook

from .releve import _nombre, _sans_accent, groupe_crm


class FichierDfcInvalide(ValueError):
    """Le fichier n'a pas la forme attendue."""


# Colonne « banque » du classeur de la DFC -> groupe de banque, le même que
# celui déduit du code banque du CRM (releve.GROUPES_CRM).
BANQUES_DFC = {
    "BNM": "BNM", "GBM": "GBM", "BEA": "BEA", "BAMIS": "BAMIS", "ORABANK": "OBM", "ORA BANK": "OBM",
    "BCI": "BCI", "BMCI": "BMCI", "TRESOR": "TRESOR231", "STE GLE": "SGM", "SGM": "SGM",
    "CHINGUITY BQ": "CHINGUITTY", "CCP": "CCP", "NBM": "NBM", "BPM": "BPM", "BMS": "BMS",
    "ABM": "ABM", "IBM": "IBM", "BIM": "BIM", "DFI": "DFI", "BFI": "BFI", "BMI": "BMI",
}

# Résultats, du plus rassurant au plus préoccupant.
RESULTATS = [
    ("DFC_CONFORME", "Conforme"),
    ("DFC_BANQUE", "Banque différente"),
    ("DFC_DEPENSE", "Dépense différente"),
    ("DFC_VERSE", "Versé différent"),
    ("DFC_ENCAISSE", "Encaissé différent"),
    ("DFC_NON_SAISI", "Non saisi par la DFC"),
    ("DFC_ABSENT_CRM", "Absent du CRM"),
]
LIBELLES = dict(RESULTATS)
# Du plus grave au moins grave : le premier trouvé devient le résultat du jour.
_PRIORITE = ["DFC_ENCAISSE", "DFC_VERSE", "DFC_DEPENSE", "DFC_BANQUE"]

_MOIS = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août", "Septembre", "Octobre",
         "Novembre", "Décembre"]
_ENCAISSEMENTS = ("ESPECE", "CHQ", "VIRMT")
# Première colonne après les banques, puis après les dépenses.
_FIN_BANQUES = ("GASOIL", "BUREAU", "THE", "ENT LOC", "ENT RES", "REM", "ENCAISSEMENT")
_FIN_DEPENSES = ("REM", "ENCAISSEMENT", "VERSEMENT")


def centre_du_nom(nom_fichier: str, centres_connus: set[str]) -> str:
    """Numéro du centre lu dans le nom du fichier (« 51.xlsx », « centre_51.xlsx »)."""
    base = re.sub(r"\.[A-Za-z0-9]+$", "", str(nom_fichier or "").replace("\\", "/").split("/")[-1])
    nombres = [str(int(n)) for n in re.findall(r"\d+", base)]
    for n in nombres:
        if n in centres_connus:
            return n
    if not centres_connus:
        for n in nombres:
            if len(n) <= 3:
                return n
    if not nombres:
        raise FichierDfcInvalide(
            f"« {nom_fichier} » : le nom du fichier doit contenir le numéro du centre, par exemple 51.xlsx.")
    raise FichierDfcInvalide(
        f"« {nom_fichier} » : aucun numéro de ce nom ne correspond à un centre connu ({', '.join(nombres)}).")


def lire_fichier(contenu: bytes) -> dict:
    """Lit le classeur d'un centre.

    Retourne les jours saisis (au moins un montant) et la période couverte par
    la feuille (du premier au dernier jour daté, saisi ou non)."""
    try:
        classeur = load_workbook(io.BytesIO(contenu), read_only=True, data_only=True)
    except Exception as exc:
        raise FichierDfcInvalide(f"Le fichier n'est pas un classeur Excel lisible : {exc}") from exc

    jours: dict[date, dict] = {}
    dates: list[date] = []
    for feuille in classeur.worksheets:
        colonnes = None
        for rang in feuille.iter_rows(values_only=True):
            if rang and rang[0] is not None and _sans_accent(rang[0]) == "DATE ENC":
                noms = [_sans_accent(v) if v is not None else "" for v in rang]
                if not all(n in noms for n in _ENCAISSEMENTS):
                    continue
                debut = noms.index("VIRMT") + 1
                fin_banques = min([noms.index(n) for n in _FIN_BANQUES if n in noms[debut:]] or [len(noms)])
                fin_depenses = min([i for i, n in enumerate(noms) if i >= fin_banques and n in _FIN_DEPENSES]
                                   or [fin_banques])
                colonnes = {
                    "especes": noms.index("ESPECE"), "cheques": noms.index("CHQ"), "virements": noms.index("VIRMT"),
                    "banques": [(i, noms[i]) for i in range(debut, fin_banques) if noms[i]],
                    "depenses": list(range(fin_banques, fin_depenses)),
                    "rem": noms.index("REM") if "REM" in noms[fin_banques:] else None,
                    "observation": noms.index("OBSERVATION") if "OBSERVATION" in noms else None,
                }
                continue
            if colonnes is None or not rang or not isinstance(rang[0], (datetime, date)):
                continue
            jour = rang[0].date() if isinstance(rang[0], datetime) else rang[0]
            dates.append(jour)

            def valeur(i):
                return _nombre(rang[i]) if i is not None and i < len(rang) else 0.0

            banques: dict[str, float] = {}
            for i, nom in colonnes["banques"]:
                montant = valeur(i)
                if montant:
                    groupe = BANQUES_DFC.get(nom, nom)
                    banques[groupe] = round(banques.get(groupe, 0.0) + montant, 3)
            observation = None
            if colonnes["observation"] is not None and colonnes["observation"] < len(rang):
                texte = str(rang[colonnes["observation"]] or "").strip()
                observation = texte or None
            ligne = {
                "jour": jour,
                "especes": round(valeur(colonnes["especes"]), 3),
                "cheques": round(valeur(colonnes["cheques"]), 3),
                "virements": round(valeur(colonnes["virements"]), 3),
                "banques": banques,
                "verse": round(sum(banques.values()), 3),
                "depenses": round(sum(valeur(i) for i in colonnes["depenses"]), 3),
                "rem": round(valeur(colonnes["rem"]), 3),
                "observation": observation,
            }
            if any(ligne[c] for c in ("especes", "cheques", "virements", "verse", "depenses", "rem")):
                jours[jour] = ligne   # un jour répété : la dernière ligne l'emporte
    if not dates:
        raise FichierDfcInvalide(
            "Aucune ligne lue : la feuille doit contenir une ligne d'en-tête « DATE ENC, DATE VERS, ESPECE, CHQ, "
            "VIRMT, … » suivie d'une ligne par jour.")
    return {"jours": [jours[j] for j in sorted(jours)], "couverture": (min(dates), max(dates))}


def _banques_texte(banques: dict[str, float]) -> str | None:
    return " + ".join(sorted(b for b, m in banques.items() if m)) or None


def _pct(part, total):
    return round(100.0 * part / total, 1) + 0.0 if total else None   # + 0.0 : jamais « -0,0 »


def croiser_jours(saisies: list[dict], controle: list[dict], versements: list[dict],
                  noms_centres: dict[str, str], tolerance: float = 1.0) -> list[dict]:
    """Compare, par centre et par jour, la saisie de la DFC et le CRM.

    `saisies` : jours saisis par la DFC ; `controle` : lignes centre-jour du
    contrôle (récapitulatif de caisse du CRM) ; `versements` : versements
    d'espèces du CRM, pour la banque. Seuls les jours couverts par un fichier
    importé doivent être fournis.

    Un jour où la caisse a travaillé dans le CRM et que la DFC n'a pas saisi est
    un jour pour lequel le caissier n'a pas envoyé ses pièces à la DFC (ou que la
    DFC n'a pas saisi) : c'est le résultat le plus important de ce croisement,
    qu'il s'agisse d'un mois ancien ou des derniers jours.
    """
    dfc = {(str(l["centre"]), str(l["jour"])[:10]): l for l in saisies}
    crm = {}
    for l in controle:
        actif = any(abs(l.get(c) or 0) >= 0.005 for c in (
            "recap_especes", "recap_cheques", "recap_virements", "recap_vers_especes", "recap_vers_cheques",
            "depenses"))
        if actif:
            crm[(str(l["centre"]), str(l["jour"])[:10])] = l
    banques_crm: dict[tuple, dict[str, float]] = {}
    for v in versements:
        if v.get("statut") != "VALIDE":
            continue
        cle = (str(v["centre"]), str(v["date_versement"])[:10])
        groupe = groupe_crm(v.get("code_banque"))
        b = banques_crm.setdefault(cle, {})
        b[groupe] = round(b.get(groupe, 0.0) + float(v.get("montant") or 0), 3)

    lignes = []
    for cle in sorted(set(dfc) | set(crm), key=lambda c: (c[1], int(c[0]) if c[0].isdigit() else 0, c[0])):
        centre, jour = cle
        d, c = dfc.get(cle), crm.get(cle)
        bq_dfc = d["banques"] if d else {}
        bq_crm = banques_crm.get(cle, {})
        ligne = {
            "jour": jour, "centre": centre,
            "nom_centre": (c or {}).get("nom_centre") or noms_centres.get(centre),
            "encaisse_dfc": round(d["especes"] + d["cheques"] + d["virements"], 3) if d else None,
            "encaisse_crm": round(c["recap_especes"] + c["recap_cheques"] + c["recap_virements"], 3) if c else None,
            "verse_dfc": d["verse"] if d else None,
            "verse_crm": round(c["recap_vers_especes"] + c["recap_vers_cheques"], 3) if c else None,
            "banque_dfc": _banques_texte(bq_dfc), "banque_crm": _banques_texte(bq_crm),
            "depenses_dfc": d["depenses"] if d else None, "depenses_crm": c["depenses"] if c else None,
            "observation": d["observation"] if d else None,
        }
        anomalies = []
        if d is None:
            code = "DFC_NON_SAISI"
        elif c is None:
            code = "DFC_ABSENT_CRM"
        else:
            if any(abs(d[a] - c[b]) >= tolerance for a, b in (
                    ("especes", "recap_especes"), ("cheques", "recap_cheques"), ("virements", "recap_virements"))):
                anomalies.append("DFC_ENCAISSE")
            if abs(ligne["verse_dfc"] - ligne["verse_crm"]) >= tolerance:
                anomalies.append("DFC_VERSE")
            if abs(d["depenses"] - c["depenses"]) >= tolerance:
                anomalies.append("DFC_DEPENSE")
            # La banque n'est comparée que si le CRM a des versements d'espèces ce
            # jour-là et que le montant versé concorde : sinon l'écart de montant
            # explique déjà la différence.
            if bq_dfc and bq_crm and "DFC_VERSE" not in anomalies and any(
                    abs(bq_dfc.get(b, 0.0) - bq_crm.get(b, 0.0)) >= tolerance for b in set(bq_dfc) | set(bq_crm)):
                anomalies.append("DFC_BANQUE")
            code = next((a for a in _PRIORITE if a in anomalies), "DFC_CONFORME")
        # Écart = CRM − DFC : ce que le CRM contient et que la DFC n'a pas. Un jour
        # absent d'un côté y compte pour zéro : il fait partie de l'écart. Un écart
        # positif est de l'argent dont la DFC n'a pas reçu les pièces.
        ligne["ecart_encaisse"] = round((ligne["encaisse_crm"] or 0) - (ligne["encaisse_dfc"] or 0), 3)
        ligne["ecart_verse"] = round((ligne["verse_crm"] or 0) - (ligne["verse_dfc"] or 0), 3)
        ligne["resultat_code"] = code
        ligne["resultat"] = LIBELLES[code]
        ligne["anomalies"] = ", ".join(LIBELLES[a] for a in anomalies) or LIBELLES[code]
        lignes.append(ligne)
    return lignes


def resumer(lignes: list[dict], tolerance: float = 1.0) -> dict:
    """Résultats, totaux et tableau par mois d'un ensemble de jours croisés."""
    nb = len(lignes)
    synthese = []
    for code, libelle in RESULTATS:
        choisies = [l for l in lignes if l["resultat_code"] == code]
        synthese.append({
            "code": code, "libelle": libelle, "nb": len(choisies), "pct_nb": _pct(len(choisies), nb),
            "montant_dfc": round(sum(l["verse_dfc"] or 0 for l in choisies), 3),
            "montant_crm": round(sum(l["verse_crm"] or 0 for l in choisies), 3),
        })

    # Totaux de la période : tout ce que la DFC a saisi face à tout ce que le CRM
    # contient. Un jour ou un mois que la DFC n'a pas saisi compte pour zéro de
    # son côté : il fait donc partie de l'écart.
    def somme(champ, source=None):
        return round(sum(l[champ] or 0 for l in (lignes if source is None else source)), 3)

    communs = [l for l in lignes if l["encaisse_dfc"] is not None and l["encaisse_crm"] is not None]
    non_saisis = [l for l in lignes if l["resultat_code"] == "DFC_NON_SAISI"]
    totaux = {
        "nb_jours": nb, "nb_communs": len(communs),
        "encaisse_dfc": somme("encaisse_dfc"), "encaisse_crm": somme("encaisse_crm"),
        "verse_dfc": somme("verse_dfc"), "verse_crm": somme("verse_crm"),
        "nb_non_saisis": len(non_saisis), "encaisse_non_saisi": somme("encaisse_crm", non_saisis),
        "verse_non_saisi": somme("verse_crm", non_saisis),
    }
    totaux["ecart_encaisse"] = round(totaux["encaisse_crm"] - totaux["encaisse_dfc"], 3)
    totaux["ecart_verse"] = round(totaux["verse_crm"] - totaux["verse_dfc"], 3)
    totaux["pct_ecart_encaisse"] = _pct(totaux["ecart_encaisse"], totaux["encaisse_crm"])
    totaux["pct_ecart_verse"] = _pct(totaux["ecart_verse"], totaux["verse_crm"])

    # Par mois et par centre : le contrôle se lit d'abord mois par mois.
    groupes: dict[tuple, list[dict]] = {}
    for l in lignes:
        groupes.setdefault((l["centre"], l["jour"][:7]), []).append(l)
    mois = []
    for (centre, periode), jours in sorted(groupes.items(), key=lambda g: (
            g[0][1], int(g[0][0]) if g[0][0].isdigit() else 0, g[0][0])):
        saisis = [l for l in jours if l["encaisse_dfc"] is not None]
        au_crm = [l for l in jours if l["encaisse_crm"] is not None]
        m = {
            "mois": periode, "mois_libelle": f"{_MOIS[int(periode[5:7]) - 1]} {periode[:4]}", "centre": centre, "nom_centre": next((l["nom_centre"] for l in jours if l["nom_centre"]), None),
            "nb_jours_dfc": len(saisis), "nb_jours_crm": len(au_crm),
            "encaisse_dfc": somme("encaisse_dfc", jours), "encaisse_crm": somme("encaisse_crm", jours),
            "verse_dfc": somme("verse_dfc", jours), "verse_crm": somme("verse_crm", jours),
            "nb_jours_non_saisis": sum(1 for l in jours if l["resultat_code"] == "DFC_NON_SAISI"),
            "nb_jours_ecart": sum(1 for l in jours if l["resultat_code"] != "DFC_CONFORME"),
        }
        m["ecart_encaisse"] = round(m["encaisse_crm"] - m["encaisse_dfc"], 3)
        m["ecart_verse"] = round(m["verse_crm"] - m["verse_dfc"], 3)
        if not saisis:
            code = "DFC_NON_SAISI"   # mois entier sans saisie
        elif not au_crm:
            code = "DFC_ABSENT_CRM"
        elif m["nb_jours_non_saisis"]:
            code = "DFC_NON_SAISI"   # le plus important : des jours dont les pièces ne sont pas arrivées
        elif abs(m["ecart_encaisse"]) >= tolerance:
            code = "DFC_ENCAISSE"
        elif abs(m["ecart_verse"]) >= tolerance:
            code = "DFC_VERSE"
        else:
            # Totaux du mois identiques : le mois garde le résultat le plus grave de ses jours.
            codes = {l["resultat_code"] for l in jours}
            code = next((c for c in ("DFC_ABSENT_CRM", "DFC_NON_SAISI", "DFC_ENCAISSE", "DFC_VERSE", "DFC_DEPENSE",
                                     "DFC_BANQUE") if c in codes), "DFC_CONFORME")
        m["resultat_code"] = code
        if code == "DFC_NON_SAISI" and not saisis:
            m["resultat"] = "Mois non saisi par la DFC"
        elif code == "DFC_NON_SAISI":
            n = m["nb_jours_non_saisis"]
            m["resultat"] = f"{n} jour{'s' if n > 1 else ''} non saisi{'s' if n > 1 else ''} par la DFC"
        else:
            m["resultat"] = LIBELLES[code]
        mois.append(m)
    return {"lignes": lignes, "synthese": synthese, "totaux": totaux, "mois": mois}
