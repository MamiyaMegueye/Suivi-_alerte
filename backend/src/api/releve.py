"""Relevé bancaire : lecture du fichier, classement des lignes et rapprochement
avec les versements d'espèces du CRM.

Le relevé est une feuille Excel avec les colonnes Banque, Mois, Date, Valeur,
Libellé, Debit, Credit. Il ne contient ni le code du centre ni le numéro de
versement du CRM : le rapprochement se fait donc sur la banque, le montant et
la date, en plusieurs passes, de la plus sûre à la plus large.
"""
import io
import itertools
import re
import unicodedata
from datetime import date, datetime, timedelta

from openpyxl import load_workbook


class ReleveInvalide(ValueError):
    """Le fichier n'a pas la forme attendue."""


# --------------------------------------------------------------------------- banques
# Abréviation du relevé -> groupe de banque, et code banque du CRM -> même groupe.
# Deux lignes sont de la « même banque » quand elles ont le même groupe.
GROUPES_RELEVE = {
    "BMCI": "BMCI", "BEA": "BEA", "AMANA": "BEA", "BCI": "BCI", "CCP": "CCP",
    "BMI": "BMI", "BMI PROJET": "BMI", "BIM": "BIM", "BNM": "BNM", "OBM": "OBM",
    "SGM": "SGM", "BPM": "BPM", "DAO BPM": "BPM", "DAO": "BPM", "BFI": "BFI",
    "BMS": "BMS", "TRESOR231": "TRESOR231", "ABM": "ABM", "IBM": "IBM",
    "GBM": "GBM", "BAMIS": "BAMIS",
}
GROUPES_CRM = {
    "5006": "BMCI", "5014": "BEA", "5044": "BEA", "5012": "BCI", "5029": "CCP",
    "5010": "BMI", "5022": "BIM", "5002": "BNM", "5019": "OBM", "5020": "SGM",
    "5021": "BPM", "5040": "BFI", "5001": "BMS", "5030": "TRESOR231",
    "5023": "ABM", "5041": "IBM", "5009": "GBM", "5008": "BAMIS",
}

VERSEMENT = "VERSEMENT ESPECES"

# Résultats, du plus rassurant au plus préoccupant.
RESULTATS = [
    ("MEME_JOUR", "Même banque, même jour"),
    ("DECALE", "Même banque, quelques jours d'écart"),
    ("GROUPE", "Dépôt groupé"),
    ("FRACTIONNE", "Dépôt en  plusieurs fois"),
    ("ARRONDI", "Montant arrondi"),
    ("TRES_DECALE", "Plus de 10 jours d'écart"),
    ("AUTRE_BANQUE", "Autre banque que celle déclarée"),
    ("MONTANT_DIFFERENT", "Montant différent"),
    ("NON_RETROUVE", "Non retrouvé dans le relevé"),
]
LIBELLES = dict(RESULTATS)
# Résultats où le versement est retrouvé sans réserve.
CONFORMES = {"MEME_JOUR", "DECALE", "GROUPE", "FRACTIONNE", "ARRONDI"}

# Marges de lecture autour de la période demandée : un versement de fin de mois
# peut être déposé au début du mois suivant, et l'inverse.
MARGE_CRM_AVANT, MARGE_CRM_APRES = 45, 20
MARGE_RELEVE = 70


# --------------------------------------------------------------------------- lecture du fichier
def _sans_accent(texte) -> str:
    texte = unicodedata.normalize("NFD", str(texte))
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texte.upper()).strip()


def _nombre(valeur) -> float:
    if valeur is None:
        return 0.0
    if isinstance(valeur, (int, float)):
        return float(valeur)
    texte = str(valeur).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not texte:
        return 0.0
    try:
        return float(texte)
    except ValueError:
        return 0.0


def _date(valeur) -> date | None:
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    if isinstance(valeur, str):
        m = re.match(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\s*$", valeur)
        if m:
            jour, mois, annee = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if annee < 100:
                annee += 2000
            try:
                return date(annee, mois, jour)
            except ValueError:
                return None
    return None


def groupe_releve(banque: str) -> str:
    cle = _sans_accent(banque)
    return GROUPES_RELEVE.get(cle, cle)


def groupe_crm(code_banque) -> str:
    code = str(code_banque or "").strip()
    return GROUPES_CRM.get(code, f"CRM {code}" if code else "SANS BANQUE")


def classer(libelle: str, au_credit: bool) -> str:
    """Type d'opération d'une ligne du relevé, d'après les mots de son libellé."""
    texte = _sans_accent(libelle)
    annulation = bool(re.search(
        r"REVERSE|INVERSION|EXTOURNE|\bEXT REGL|ANNUL|VOID|REDRESSEMENT|REJET|IMPAYE", texte))
    if not au_credit:
        # Un versement au débit est un versement que la banque a retiré du compte.
        if re.search(r"VERS(EMENT)? ?(EN )?(ESP|DEPLACE|D.PLACE)|^VERSEMENT|DEPOT EN ESP", texte) \
                and "RETRAIT" not in texte:
            return "VERSEMENT ANNULE"
        return "ANNULATION" if annulation else "DEBIT"
    if annulation:
        return "ANNULATION"
    if au_credit:
        if re.search(r"VERS(EMENT|MENT)?S? (EN )?ESP|VERS ESPECE|VER ESP|DEPOT EN ESP"
                     r"|VERSEMENT (DEPLACE|D.PLACE|EFFECTUE|MRU|\(?\*|&|DOPLACE)|VERSEMENT [\(\*&]"
                     r"|^VERSEMENT|DEPOT CLIENT|DEPOT SUR COMPTE|DIVERS VERSEMENT", texte):
            return VERSEMENT
        if re.search(r"CHQ|CHEQ|CHEQUE|\bCH N|REMISE ORDRE|REMISE CHE|REM CHQ|\bCHO\b|REGLEMENT CH ", texte):
            return "REMISE CHEQUE"
        if re.search(r"MASRVI|SEDAD|AMANTY|BIMBANK|CLICK|GAZA|GIMTEL|BANKILY|MOBILE|DIGITAL|TRF SOLDE|SOLED|NXT", texte):
            return "PAIEMENT ELECTRONIQUE"
        if re.search(r"FACT|\bFAC\b|\bFA\b|\bFT\b|RGLT|REGL|\bREG\b|RGL|CONSO|EAU", texte):
            return "REGLEMENT FACTURE"
        if re.search(r"MANDAT|CHQTR|PRECOMPTE|/\d+T\d+", texte):
            return "TRESOR"
        if re.search(r"VIR|VRT|TRANSFERT|BKL|VI\.C|TFR|BEN ", texte):
            return "VIREMENT RECU"
        return "AUTRE CREDIT"
    return "DEBIT"


def lire_fichier(contenu: bytes) -> list[dict]:
    """Lit le classeur du relevé et retourne ses lignes, classées."""
    try:
        classeur = load_workbook(io.BytesIO(contenu), read_only=True, data_only=True)
    except Exception as exc:
        raise ReleveInvalide(f"Le fichier n'est pas un classeur Excel lisible : {exc}") from exc

    lignes: list[dict] = []
    for feuille in classeur.worksheets:
        colonnes: dict[str, int] | None = None
        for rang in feuille.iter_rows(values_only=True):
            if colonnes is None:
                noms = {_sans_accent(v): i for i, v in enumerate(rang) if v is not None}
                if {"BANQUE", "DATE", "LIBELLE"} <= set(noms) and ("CREDIT" in noms or "DEBIT" in noms):
                    colonnes = noms
                continue

            def cellule(nom):
                i = colonnes.get(nom)
                return rang[i] if i is not None and i < len(rang) else None

            banque = cellule("BANQUE")
            jour = _date(cellule("DATE"))
            if banque is None or jour is None:
                continue
            debit, credit = _nombre(cellule("DEBIT")), _nombre(cellule("CREDIT"))
            if debit == 0 and credit == 0:
                continue
            libelle = re.sub(r"\s+", " ", str(cellule("LIBELLE") or "")).strip()
            banque = re.sub(r"\s+", " ", str(banque)).strip()
            lignes.append({
                "ligne": len(lignes) + 1,
                "banque": banque,
                "groupe": groupe_releve(banque),
                "date_operation": jour,
                "date_valeur": _date(cellule("VALEUR")),
                "libelle": libelle,
                "debit": round(debit, 3),
                "credit": round(credit, 3),
                "classe": classer(libelle, credit > 0),
            })
    if not lignes:
        raise ReleveInvalide(
            "Aucune ligne lue : le fichier doit contenir les colonnes Banque, Date, Libellé, Debit et Credit."
        )
    return lignes


# --------------------------------------------------------------------------- rapprochement
def _lieux(lignes: list[dict]) -> dict:
    """Lieu du dépôt quand le libellé le donne : l'agence pour la BMCI
    (« ... Agence Mellah »), la localité pour le CCP (« Versement déplacé - Boutilmit »).
    Retourne {numéro de ligne: lieu}. Les autres banques n'indiquent pas de lieu.

    Seul le premier mot est gardé : le nom du déposant suit parfois celui de
    l'agence (« Agence Mellah FATIMETOU CHEIKH »), et ce premier mot suffit à
    distinguer une ville ou un quartier d'un autre."""
    lieux = {}
    for l in lignes:
        if l["groupe"] not in ("BMCI", "CCP") or (l.get("credit") or 0) <= 0:
            continue
        texte = _sans_accent(l["libelle"])
        suite = None
        if "AGENCE " in texte:
            suite = texte.split("AGENCE ", 1)[1]
        elif texte.startswith("VERSEMENT") and "-" in texte:
            suite = texte.split("-", 1)[1]
        mots = re.findall(r"[A-Z0-9]+", suite or "")
        if mots:
            lieux[l["ligne"]] = mots[0]
    return lieux


def _jour(valeur) -> date:
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    return date.fromisoformat(str(valeur)[:10])


def rapprocher(versements: list[dict], lignes_releve: list[dict],
               couverture: tuple[date, date] | None = None) -> tuple[dict, set]:
    """Cherche chaque versement validé du CRM dans les crédits du relevé.

    Retourne, par indice de versement, le résultat trouvé, et l'ensemble des
    lignes du relevé utilisées. Une ligne du relevé ne sert qu'une fois.

    `couverture` : première et dernière date du relevé. Un versement daté en
    dehors (fin décembre pour un relevé qui commence en janvier) a été déposé,
    pour l'essentiel, hors du relevé : il ne peut prendre une ligne du relevé
    que par les passes les plus sûres (montant exact à quelques jours, ou
    dépôt groupé), jamais par un rapprochement large.
    """
    V = []
    for i, v in enumerate(versements):
        if v.get("statut") != "VALIDE":
            continue
        jour = _jour(v["date_versement"])
        V.append({"i": i, "g": groupe_crm(v.get("code_banque")), "m": round(float(v.get("montant") or 0), 2),
                  "d": jour, "centre": str(v.get("centre") or ""),
                  "hors": bool(couverture and not (couverture[0] <= jour <= couverture[1]))})
    lieux_lignes = _lieux(lignes_releve)
    R = []
    for l in lignes_releve:
        if (l.get("credit") or 0) <= 0:
            continue
        R.append({"j": l["ligne"], "g": l["groupe"], "m": round(float(l["credit"]), 2),
                  "d": _jour(l["date_operation"]), "vers": l["classe"] == VERSEMENT,
                  "lieu": lieux_lignes.get(l["ligne"])})
    credits_vers = [r for r in R if r["vers"]]

    pris: set = set()
    res: dict = {}
    # Lieux où chaque centre dépose d'habitude, appris sur les rapprochements
    # sûrs (montant exact, date proche). Ils servent à écarter les coïncidences :
    # un dépôt fait à l'agence d'une autre ville n'est pas rattaché à un centre
    # par une simple addition de montants.
    habitudes: dict = {}

    def apprendre():
        par_indice = {v["i"]: v for v in V}
        par_ligne = {r["j"]: r for r in R}
        for i, r0 in res.items():
            for j in r0["lignes"]:
                lieu = par_ligne[j]["lieu"]
                if lieu:
                    habitudes.setdefault(par_indice[i]["centre"], set()).add(lieu)

    def plausible(v, r):
        """Faux si le dépôt est fait dans un lieu où ce centre ne dépose jamais."""
        if not r["lieu"]:
            return True
        connus = habitudes.get(v["centre"])
        return not connus or r["lieu"] in connus

    def exact(code, candidats, meme_banque, jmin, jmax, large=False):
        index: dict = {}
        for r in candidats:
            index.setdefault((r["g"], r["m"]) if meme_banque else r["m"], []).append(r)
        for v in sorted(V, key=lambda v: v["d"]):
            if v["i"] in res or (large and v["hors"]):
                continue
            meilleur = None
            for r in index.get((v["g"], v["m"]) if meme_banque else v["m"], ()):
                if r["j"] in pris:
                    continue
                ecart = (r["d"] - v["d"]).days
                if jmin <= ecart <= jmax and (meilleur is None or abs(ecart) < abs(meilleur[1])):
                    meilleur = (r, ecart)
            if meilleur:
                etiquette = code
                if code == "TRES_DECALE" and -3 <= meilleur[1] <= 10:
                    etiquette = "DECALE"
                res[v["i"]] = {"code": etiquette, "lignes": [meilleur[0]["j"]], "ecart_jours": meilleur[1],
                               "montant_banque": v["m"]}
                pris.add(meilleur[0]["j"])

    def groupes(jours_avant, jours_apres, large=False):
        """Plusieurs versements du même centre déposés en une seule fois."""
        libres: dict = {}
        for v in V:
            if v["i"] not in res and not (large and v["hors"]):
                libres.setdefault(v["g"], []).append(v)
        for r in sorted((r for r in credits_vers if r["j"] not in pris), key=lambda r: r["d"]):
            d1, d2 = r["d"] - timedelta(days=jours_avant), r["d"] + timedelta(days=jours_apres)
            par_centre: dict = {}
            for v in libres.get(r["g"], ()):
                if v["i"] not in res and d1 <= v["d"] <= d2 and v["m"] < r["m"] + 0.5 and plausible(v, r):
                    par_centre.setdefault(v["centre"], []).append(v)
            trouve = None
            for centre in sorted(par_centre):
                groupe = par_centre[centre]
                if len(groupe) < 2 or len(groupe) > 14 or sum(v["m"] for v in groupe) < r["m"] - 0.5:
                    continue
                for taille in (2, 3, 4, 5):
                    for combinaison in itertools.combinations(groupe, taille):
                        if abs(sum(v["m"] for v in combinaison) - r["m"]) <= 0.5:
                            etendue = (max(v["d"] for v in combinaison) - min(v["d"] for v in combinaison)).days
                            if trouve is None or etendue < trouve[1]:
                                trouve = (combinaison, etendue)
                    if trouve:
                        break
                if trouve:
                    break
            if trouve:
                # Chaque versement porte sa part du dépôt (le reste d'arrondi va au
                # dernier) : la somme des parts est exactement le dépôt du relevé,
                # et la colonne « Montant banque » peut être additionnée.
                reste = round(r["m"] - sum(v["m"] for v in trouve[0]), 2)
                for k, v in enumerate(trouve[0]):
                    part = v["m"] + (reste if k == len(trouve[0]) - 1 else 0.0)
                    res[v["i"]] = {"code": "GROUPE", "lignes": [r["j"]], "ecart_jours": (r["d"] - v["d"]).days,
                                   "montant_banque": round(part, 2)}
                pris.add(r["j"])

    def fractionnes(jours_avant, jours_apres):
        """Un versement du CRM déposé en plusieurs fois."""
        libres: dict = {}
        for r in credits_vers:
            if r["j"] not in pris:
                libres.setdefault(r["g"], []).append(r)
        for v in sorted(V, key=lambda v: -v["m"]):
            if v["i"] in res or v["hors"]:
                continue
            d1, d2 = v["d"] - timedelta(days=jours_avant), v["d"] + timedelta(days=jours_apres)
            cand = [r for r in libres.get(v["g"], ())
                    if r["j"] not in pris and d1 <= r["d"] <= d2 and r["m"] < v["m"] + 0.5 and plausible(v, r)]
            if len(cand) < 2 or len(cand) > 40 or sum(r["m"] for r in cand) < v["m"] - 0.5:
                continue
            cand = sorted(cand, key=lambda r: abs((r["d"] - v["d"]).days))[:16]
            trouve = None
            for taille in (2, 3, 4):
                for combinaison in itertools.combinations(cand, taille):
                    if abs(sum(r["m"] for r in combinaison) - v["m"]) <= 0.5:
                        # Un dépôt en plusieurs fois se fait au même endroit.
                        if len({r["lieu"] for r in combinaison if r["lieu"]}) > 1:
                            continue
                        cout = sum(abs((r["d"] - v["d"]).days) for r in combinaison)
                        if trouve is None or cout < trouve[1]:
                            trouve = (combinaison, cout)
                if trouve:
                    break
            if trouve:
                lignes = sorted(trouve[0], key=lambda r: r["d"])
                res[v["i"]] = {"code": "FRACTIONNE", "lignes": [r["j"] for r in lignes],
                               "ecart_jours": (lignes[-1]["d"] - v["d"]).days,
                               "montant_banque": round(sum(r["m"] for r in lignes), 2)}
                pris.update(r["j"] for r in lignes)

    def proche(code, tolerance, part, jmin, jmax):
        """Même banque et date proche, montant légèrement différent."""
        libres: dict = {}
        for r in credits_vers:
            if r["j"] not in pris:
                libres.setdefault(r["g"], []).append(r)
        for v in sorted(V, key=lambda v: -v["m"]):
            if v["i"] in res or v["hors"]:
                continue
            meilleur = None
            for r in libres.get(v["g"], ()):
                if r["j"] in pris:
                    continue
                ecart = (r["d"] - v["d"]).days
                diff = abs(r["m"] - v["m"])
                if jmin <= ecart <= jmax and diff <= tolerance and diff <= part * v["m"] and plausible(v, r):
                    note = diff * 10 + abs(ecart)
                    if meilleur is None or note < meilleur[2]:
                        meilleur = (r, ecart, note)
            if meilleur:
                res[v["i"]] = {"code": code, "lignes": [meilleur[0]["j"]], "ecart_jours": meilleur[1],
                               "montant_banque": meilleur[0]["m"]}
                pris.add(meilleur[0]["j"])

    exact("MEME_JOUR", credits_vers, True, 0, 0)
    exact("DECALE", credits_vers, True, -3, 10)
    exact("DECALE", R, True, -3, 10)                 # crédit libellé autrement que « versement »
    apprendre()
    # Un montant exact dans la même banque, même à deux ou trois semaines, est
    # plus probant qu'une addition de montants : il passe avant les regroupements.
    exact("TRES_DECALE", credits_vers, True, -10, 20, large=True)
    groupes(10, 3)
    fractionnes(3, 10)
    proche("ARRONDI", 10, 1.0, -3, 10)
    exact("TRES_DECALE", credits_vers, True, -20, 45, large=True)
    groupes(20, 3, large=True)
    exact("AUTRE_BANQUE", credits_vers, False, -3, 10, large=True)
    proche("MONTANT_DIFFERENT", 500, 0.03, -3, 10)
    return res, pris


def _pct(part, total):
    return round(100.0 * part / total, 1) if total else None


def bilan(versements: list[dict], lignes_releve: list[dict], debut: date, fin: date,
          centre: str | None = None, couverture: tuple[date, date] | None = None) -> dict:
    """Rapproche puis présente le résultat pour la période et le centre demandés."""
    res, pris = rapprocher(versements, lignes_releve, couverture)
    par_ligne = {l["ligne"]: l for l in lignes_releve}

    lignes = []
    for i, v in enumerate(versements):
        jour = _jour(v["date_versement"])
        if v.get("statut") != "VALIDE" or not (debut <= jour <= fin):
            continue
        if centre and str(v.get("centre") or "").strip() != centre:
            continue
        r = res.get(i)
        code = r["code"] if r else "NON_RETROUVE"
        lus = [par_ligne[j] for j in r["lignes"]] if r else []
        montant = round(float(v.get("montant") or 0), 3)
        montant_banque = r["montant_banque"] if r else None
        lignes.append({
            "date_versement": jour.isoformat(),
            "centre": v.get("centre"), "nom_centre": v.get("nom_centre"), "matricule": v.get("matricule"),
            "num_versement": v.get("num_versement"), "code_banque": v.get("code_banque"),
            "nom_banque": v.get("nom_banque"), "banque_groupe": groupe_crm(v.get("code_banque")),
            "montant": montant,
            "resultat_code": code, "resultat": LIBELLES[code],
            "date_banque": lus[-1]["date_operation"].isoformat() if lus else None,
            "ecart_jours": r["ecart_jours"] if r else None,
            "montant_banque": montant_banque,
            "ecart_montant": round(montant_banque - montant, 3) if r else None,
            "banque_releve": lus[0]["banque"] if lus else None,
            "libelle_releve": " + ".join(l["libelle"] for l in lus) if lus else None,
            "nb_lignes_releve": len(lus),
        })

    nb, total = len(lignes), sum(l["montant"] for l in lignes)
    synthese = []
    for code, libelle in RESULTATS:
        choisies = [l for l in lignes if l["resultat_code"] == code]
        montant = round(sum(l["montant"] for l in choisies), 3)
        synthese.append({"code": code, "libelle": libelle, "nb": len(choisies), "pct_nb": _pct(len(choisies), nb),
                         "montant": montant, "pct_montant": _pct(montant, total)})

    # Côté relevé : dépôts d'espèces de la période sans versement dans le CRM,
    # et versements passés au débit (annulés par la banque).
    depots = [l for l in lignes_releve
              if l["classe"] == VERSEMENT and (l["credit"] or 0) > 0 and debut <= _jour(l["date_operation"]) <= fin]
    sans_crm = [l for l in depots if l["ligne"] not in pris]
    annules = [l for l in lignes_releve
               if l["classe"] == "VERSEMENT ANNULE" and debut <= _jour(l["date_operation"]) <= fin]

    def ligne_releve(l, champ):
        return {"banque": l["banque"], "date_operation": _jour(l["date_operation"]).isoformat(),
                "libelle": l["libelle"], "montant": l[champ]}

    banques: dict = {}

    def banque(nom):
        return banques.setdefault(nom, {
            "banque": nom, "nb_crm": 0, "montant_crm": 0.0, "nb_trouves": 0, "nb_a_verifier": 0,
            "nb_non_retrouves": 0, "montant_non_retrouve": 0.0, "nb_releve": 0, "montant_releve": 0.0,
            "nb_sans_crm": 0, "montant_sans_crm": 0.0,
        })

    centres: dict = {}
    for l in lignes:
        b = banque(l["banque_groupe"])
        c = centres.setdefault(l["centre"], {
            "centre": l["centre"], "nom_centre": l["nom_centre"], "nb": 0, "montant": 0.0, "nb_trouves": 0,
            "nb_a_verifier": 0, "nb_non_retrouves": 0, "montant_non_retrouve": 0.0,
        })
        for cible, cle_nb, cle_montant in ((b, "nb_crm", "montant_crm"), (c, "nb", "montant")):
            cible[cle_nb] += 1
            cible[cle_montant] += l["montant"]
            if l["resultat_code"] == "NON_RETROUVE":
                cible["nb_non_retrouves"] += 1
                cible["montant_non_retrouve"] += l["montant"]
            else:
                cible["nb_trouves"] += 1
                if l["resultat_code"] not in CONFORMES:
                    cible["nb_a_verifier"] += 1
    if not centre:
        for l in depots:
            b = banque(l["groupe"])
            b["nb_releve"] += 1
            b["montant_releve"] += l["credit"]
            if l["ligne"] not in pris:
                b["nb_sans_crm"] += 1
                b["montant_sans_crm"] += l["credit"]
    for b in banques.values():
        for cle in ("montant_crm", "montant_non_retrouve", "montant_releve", "montant_sans_crm"):
            b[cle] = round(b[cle], 3)
        b["pct_trouves"] = _pct(b["nb_trouves"], b["nb_crm"])
        b["pct_non_retrouves"] = _pct(b["nb_non_retrouves"], b["nb_crm"])
    for c in centres.values():
        c["montant"] = round(c["montant"], 3)
        c["montant_non_retrouve"] = round(c["montant_non_retrouve"], 3)
        c["pct_non_retrouves"] = _pct(c["nb_non_retrouves"], c["nb"])
        c["pct_montant_non_retrouve"] = _pct(c["montant_non_retrouve"], c["montant"])

    # Écart global de la période : ce que le CRM dit versé, face à ce que la
    # banque a reçu. Pour tous les centres, la banque est le relevé lui-même :
    # dépôts d'espèces, moins les versements que la banque a annulés. Pour un
    # centre, le relevé n'indique pas le centre : seule la part retrouvée de
    # ses versements est connue.
    montant_depots = round(sum(l["credit"] for l in depots), 3)
    montant_annules = round(sum(abs(l["debit"] or 0) for l in annules), 3)
    retrouve = round(sum(l["montant_banque"] or 0 for l in lignes), 3)
    banque_globale = retrouve if centre else round(montant_depots - montant_annules, 3)
    ecart = round(total - banque_globale, 3)
    global_ = {
        "base": "retrouve" if centre else "releve",
        "nb_crm": nb, "crm": round(total, 3),
        "banque": banque_globale,
        "nb_depots": len(depots), "depots": montant_depots,
        "nb_annules": len(annules), "annules": montant_annules,
        "nb_retrouves": sum(1 for l in lignes if l["resultat_code"] != "NON_RETROUVE"), "retrouve": retrouve,
        "ecart": ecart, "pct_ecart": _pct(ecart, total),
    }

    lignes.sort(key=lambda l: (l["date_versement"], str(l["centre"] or ""), str(l["num_versement"] or "")))
    return {
        "global": global_,
        "nb_versements": nb,
        "montant_versements": round(total, 3),
        "nb_trouves": sum(1 for l in lignes if l["resultat_code"] != "NON_RETROUVE"),
        "pct_trouves": _pct(sum(1 for l in lignes if l["resultat_code"] != "NON_RETROUVE"), nb),
        "synthese": synthese,
        "banques": sorted(banques.values(), key=lambda b: -b["nb_crm"]),
        "centres": sorted(centres.values(), key=lambda c: (-c["montant_non_retrouve"], -c["nb_non_retrouves"])),
        "lignes": lignes,
        "nb_depots_releve": len(depots),
        "montant_depots_releve": round(sum(l["credit"] for l in depots), 3),
        "sans_crm": sorted((ligne_releve(l, "credit") for l in sans_crm),
                           key=lambda l: -l["montant"]) if not centre else [],
        "annules": sorted((ligne_releve(l, "debit") for l in annules),
                          key=lambda l: l["date_operation"]) if not centre else [],
    }


 