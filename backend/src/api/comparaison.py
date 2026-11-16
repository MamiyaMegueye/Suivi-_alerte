"""Lecture d'un fichier d'état de rapprochement bancaire et comparaison avec le CRM.

Format attendu (une feuille par banque, plusieurs états mensuels à la suite) :

    ETAT DE RAPPROCHEMENT DE <banque> AU <mm/aaaa>
    N° Cmpt Comptable ...            |  N° Cmpt Bancaire: <compte>
    Date | ARTICLE | Débit | Crédit  |  PIECE | ARTICLE | Débit | Crédit

- Colonne de gauche (« Passer par la banque ») : opérations vues par la banque
  et absentes des livres. Libellés de la banque : « Versement effectué par… »,
  « Remise ordre… CH N°… », « REGL FACT SNDE REF… ».
- Colonne de droite (« À passer dans nos livres ») : opérations des livres,
  avec un numéro de pièce, absentes de la banque : « VERS… », « CHQ n°/nom »,
  « VST VIR », « VST E-PAYEMENT ».

Seuls les versements d'espèces et les chèques sont recherchés dans le CRM :
les paiements d'abonnés à la banque, les virements et les e-paiements ne
passent pas par les caisses des centres.
"""
import calendar
import io
import re
import unicodedata
from datetime import date, datetime, timedelta

from openpyxl import load_workbook

from . import service

RE_TITRE = re.compile(r"ETAT\s+DE\s+RAPPROCHEMENT\s+(?:DE\s+)?(.+?)\s+AU\s+(\d{1,2})\s*/\s*(\d{4})", re.I)
RE_COMPTE = re.compile(r"C\w*PT\w*\s+BANCAIRE\s*:?\s*([\d ]{4,})", re.I)
RE_MOIS = re.compile(r"^(\d{1,2})\s*/\s*(\d{4})$")
RE_CHEQUE = re.compile(r"\b(?:CHQ|CH(?:EQUE)?)\s*(?:N\s*[O0°º]?\.?)?\s*:?\s*(\d{5,})")
RE_REFERENCE = re.compile(r"R[EA]F\s*:?\s*(\d{5,})")
RE_DATE_LIBELLE = re.compile(r"DU\s+(\d{1,2})/(\d{1,2})/(\d{4})")
RE_DATE_TEXTE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")

TYPES = {
    "VERSEMENT": "Versement d'espèces",
    "CHEQUE": "Chèque",
    "PAIEMENT_ABONNE": "Paiement d'un abonné à la banque",
    "E_PAIEMENT": "E-paiement",
    "VIREMENT": "Virement",
    "AUTRE": "Autre",
}
TYPES_RECHERCHES = ("VERSEMENT", "CHEQUE")

COTES = {
    "BANQUE": "Vu par la banque, absent des livres",
    "LIVRES": "Dans les livres, absent de la banque",
}

RESULTATS = {
    "TROUVE": "Trouvé dans le CRM",
    "TROUVE_AUTRE_COMPTE": "Trouvé sur un autre compte",
    "NON_TROUVE": "Non trouvé dans le CRM",
    "HORS_PERIMETRE": "Hors périmètre",
}

TAILLE_MAX = 20 * 1024 * 1024
NB_PERIODES_MAX = 80


class FichierInvalide(ValueError):
    """Le fichier n'est pas un état de rapprochement lisible."""


# --------------------------------------------------------------------------- outils
def _norm(texte) -> str:
    """Majuscules sans accents, espaces simples."""
    s = unicodedata.normalize("NFKD", str(texte or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().upper()


def _nombre(valeur):
    if isinstance(valeur, bool):
        return None
    if isinstance(valeur, (int, float)):
        return float(valeur)
    if isinstance(valeur, str):
        s = valeur.replace(" ", "").replace(" ", "").replace(",", ".")
        try:
            return float(s) if s else None
        except ValueError:
            return None
    return None


def _date(valeur, mois_repere=None):
    """Convertit une cellule en date. Si le jour et le mois ont été inversés
    par Excel, le mois repère de la section permet de les remettre en ordre."""
    d = None
    if isinstance(valeur, datetime):
        d = valeur.date()
    elif isinstance(valeur, date):
        d = valeur
    elif isinstance(valeur, str):
        m = RE_DATE_TEXTE.match(valeur.strip())
        if m:
            try:
                d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                d = None
    if d and mois_repere and isinstance(valeur, (datetime, date)):
        mois, annee = mois_repere
        if d.year == annee and d.month != mois and d.day == mois and d.month <= 31:
            try:
                d = date(d.year, d.day, d.month)
            except ValueError:
                pass
    return d


def _compte_norm(compte) -> str:
    return re.sub(r"\D", "", str(compte or "")).lstrip("0")


def _meme_compte(a, b) -> bool | None:
    """Vrai si les deux numéros désignent le même compte. Les numéros du CRM
    portent parfois un préfixe : on compare donc par la fin. None si inconnu."""
    a, b = _compte_norm(a), _compte_norm(b)
    if not a or not b:
        return None
    court, long_ = sorted((a, b), key=len)
    return len(court) >= 6 and long_.endswith(court)


def _classer(libelle: str) -> str:
    s = _norm(libelle)
    if "E-PAYEMENT" in s or "E-PAIEMENT" in s or "EPAYEMENT" in s:
        return "E_PAIEMENT"
    if ("FACT" in s and ("REG" in s or "RGL" in s)) or ("SNDE" in s and RE_REFERENCE.search(s)):
        return "PAIEMENT_ABONNE"
    if "REMISE" in s or s.startswith("CHQ") or "VST CHQ" in s or "CHEQUE" in s:
        return "CHEQUE"
    if "VIREMENT" in s or re.search(r"\bVIR\b", s):
        return "VIREMENT"
    if "VERSEMENT" in s or s.startswith("VERS") or re.search(r"\bV(?:ER|ST|ERS)\s+ESP", s):
        return "VERSEMENT"
    return "AUTRE"


def _tiers(libelle: str, genre: str) -> str | None:
    """Nom du déposant (versement) ou du tireur (chèque) lu dans le libellé."""
    texte = re.sub(r"\s+", " ", str(libelle or "")).strip()
    if genre == "VERSEMENT":
        m = re.search(r"\bpar\s+(.+)$", texte, re.I)
        return m.group(1).strip() if m else None
    if genre == "CHEQUE":
        m = re.search(r"ordre\s+(.+?)\s+CH\b", texte, re.I)
        if m:
            return m.group(1).strip()
        if "/" in texte:
            return texte.split("/", 1)[1].strip() or None
    return None


# --------------------------------------------------------------------------- lecture
def _colonnes(cellules: list[str]) -> dict | None:
    """Repère les colonnes à partir de la ligne d'en-tête du tableau."""
    normes = [_norm(c) for c in cellules]
    articles = [i for i, c in enumerate(normes) if c == "ARTICLE"]
    debits = [i for i, c in enumerate(normes) if c == "DEBIT"]
    credits = [i for i, c in enumerate(normes) if c == "CREDIT"]
    if not articles or not debits or not credits:
        return None
    gauche = articles[0]
    droite = articles[1] if len(articles) > 1 else None

    def apres(indices, debut, fin):
        return next((i for i in indices if debut < i < fin), None)

    limite = droite if droite is not None else len(normes) + 50
    cols = {
        "date": next((i for i, c in enumerate(normes) if c == "DATE" and i < gauche), gauche - 1),
        "article_g": gauche,
        "debit_g": apres(debits, gauche, limite),
        "credit_g": apres(credits, gauche, limite),
        "article_d": droite,
    }
    if droite is not None:
        cols["piece"] = next((i for i, c in enumerate(normes) if c == "PIECE" and i < droite), droite - 1)
        cols["debit_d"] = apres(debits, droite, len(normes) + 50)
        cols["credit_d"] = apres(credits, droite, len(normes) + 50)
    return cols


def _valeur(ligne, indice):
    if indice is None or indice < 0 or indice >= len(ligne):
        return None
    return ligne[indice]


_IGNORES = ("SOLDE", "SOUS TOTAUX", "TOTAUX", "TOTAL", "PASSER PAR", "A PASSER", "ARTICLE")


def lire_fichier(contenu: bytes) -> list[dict]:
    """Retourne la liste des états trouvés dans le fichier, avec leurs lignes."""
    if not contenu:
        raise FichierInvalide("Le fichier est vide.")
    if len(contenu) > TAILLE_MAX:
        raise FichierInvalide("Le fichier dépasse 20 Mo.")
    try:
        classeur = load_workbook(io.BytesIO(contenu), data_only=True, read_only=True)
    except Exception as exc:
        raise FichierInvalide("Le fichier n'est pas un classeur Excel (.xlsx) lisible.") from exc

    etats: list[dict] = []
    for feuille in classeur.worksheets:
        etat = None
        cols = None
        mois_g = mois_d = None
        for ligne in feuille.iter_rows(values_only=True):
            ligne = list(ligne)
            textes = [str(c).strip() if c is not None else "" for c in ligne]

            titre = next((RE_TITRE.search(_norm(t)) for t in textes if t and RE_TITRE.search(_norm(t))), None)
            if titre:
                etat = {
                    "feuille": feuille.title,
                    "banque": titre.group(1).strip(),
                    "mois": int(titre.group(2)),
                    "annee": int(titre.group(3)),
                    "compte": None,
                    "lignes": [],
                    "sous_totaux": {},
                    "soldes": {},
                }
                etats.append(etat)
                cols = None
                mois_g = mois_d = None
                continue
            if etat is None:
                continue

            for t in textes:
                m = RE_COMPTE.search(_norm(t)) if t else None
                if m:
                    etat["compte"] = re.sub(r"\s", "", m.group(1))

            nouvelles = _colonnes(textes)
            if nouvelles:
                cols = nouvelles
                continue
            if cols is None:
                continue

            for cote, prefixe in (("BANQUE", "g"), ("LIVRES", "d")):
                i_article = cols.get(f"article_{prefixe}")
                if i_article is None:
                    continue
                libelle = re.sub(r"\s+", " ", textes[i_article]).strip() if i_article < len(textes) else ""
                debit = _nombre(_valeur(ligne, cols.get(f"debit_{prefixe}")))
                credit = _nombre(_valeur(ligne, cols.get(f"credit_{prefixe}")))
                norme = _norm(libelle)

                repere = RE_MOIS.match(libelle)
                if repere:
                    valeur = (int(repere.group(1)), int(repere.group(2)))
                    if 1 <= valeur[0] <= 12:
                        if cote == "BANQUE":
                            mois_g = valeur
                        else:
                            mois_d = valeur
                    continue
                if norme.startswith("SOUS TOTAUX"):
                    etat["sous_totaux"][cote] = (debit or 0.0, credit or 0.0)
                    continue
                if norme.startswith("SOLDE AU"):
                    etat["soldes"][cote] = (debit or 0.0, credit or 0.0)
                    continue
                if any(norme.startswith(p) for p in _IGNORES):
                    continue
                if debit is None and credit is None:
                    continue
                if not (debit or credit):
                    continue

                mois_repere = mois_g if cote == "BANQUE" else mois_d
                if cote == "BANQUE":
                    jour = _date(_valeur(ligne, cols.get("date")), mois_repere)
                    piece = None
                else:
                    jour = None
                    m = RE_DATE_LIBELLE.search(norme)
                    if m:
                        try:
                            jour = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                        except ValueError:
                            jour = None
                    brut = _valeur(ligne, cols.get("piece"))
                    piece = str(int(brut)) if isinstance(brut, (int, float)) and float(brut).is_integer() else (
                        str(brut).strip() if brut not in (None, "") else None)

                genre = _classer(libelle)
                cheque = RE_CHEQUE.search(norme) if genre == "CHEQUE" else None
                reference = RE_REFERENCE.search(norme)
                etat["lignes"].append({
                    "cote": cote,
                    "date": jour,
                    "mois_origine": mois_repere,
                    "piece": piece,
                    "libelle": libelle,
                    "debit": debit,
                    "credit": credit,
                    "montant": debit if debit else credit,
                    "type": genre,
                    "num_cheque": cheque.group(1) if cheque else None,
                    "tiers": _tiers(libelle, genre),
                    "reference": reference.group(1) if reference else None,
                })

    if not etats:
        raise FichierInvalide(
            "Aucun état de rapprochement trouvé : le fichier doit contenir une ligne "
            "« ETAT DE RAPPROCHEMENT DE … AU mm/aaaa »."
        )

    # Identifiant et contrôle de lecture : la somme des lignes lues doit
    # redonner le sous-total inscrit dans le fichier.
    compteur: dict[tuple, int] = {}
    for e in etats:
        cle = (e["feuille"], e["mois"], e["annee"])
        compteur[cle] = compteur.get(cle, 0) + 1
        e["id"] = f"{e['feuille']}|{e['mois']:02d}/{e['annee']}|{compteur[cle]}"
        controles = []
        for cote in ("BANQUE", "LIVRES"):
            if cote not in e["sous_totaux"]:
                continue
            solde = e["soldes"].get(cote, (0.0, 0.0))
            lues_debit = sum(l["debit"] or 0 for l in e["lignes"] if l["cote"] == cote) + solde[0]
            lues_credit = sum(l["credit"] or 0 for l in e["lignes"] if l["cote"] == cote) + solde[1]
            attendu = e["sous_totaux"][cote]
            controles.append(abs(lues_debit - attendu[0]) < 0.5 and abs(lues_credit - attendu[1]) < 0.5)
        e["lecture_conforme"] = all(controles) if controles else None
    return etats


# --------------------------------------------------------------------------- comparaison
def _fenetre(ligne: dict, tolerance: int):
    marge = timedelta(days=tolerance)
    if ligne["date"]:
        return ligne["date"] - marge, ligne["date"] + marge
    if ligne["mois_origine"]:
        mois, annee = ligne["mois_origine"]
        if 1900 < annee < 2200:
            debut = date(annee, mois, 1)
            fin = date(annee, mois, calendar.monthrange(annee, mois)[1])
            return debut - marge, fin + marge
    return None


def _fusionner(fenetres: list[tuple]) -> list[tuple]:
    periodes: list[list] = []
    for debut, fin in sorted(fenetres):
        if periodes and debut <= periodes[-1][1] + timedelta(days=4):
            periodes[-1][1] = max(periodes[-1][1], fin)
        else:
            periodes.append([debut, fin])
    return [(d, f) for d, f in periodes]


def _lecture(cote: str, code: str, op: dict | None, ecart_montant: bool) -> str:
    if code == "HORS_PERIMETRE":
        return "Opération qui ne passe pas par les caisses des centres : non recherchée."
    if code == "NON_TROUVE":
        if cote == "BANQUE":
            return "Reçu par la banque, mais aucun versement correspondant dans le CRM."
        return "Inscrit dans les livres, mais aucun versement correspondant dans le CRM."
    texte = (
        "Reçu par la banque et enregistré dans le CRM."
        if cote == "BANQUE"
        else "Enregistré dans le CRM, mais non reçu par la banque."
    )
    if code == "TROUVE_AUTRE_COMPTE":
        texte += " Le compte bancaire du CRM est différent."
    if op and op.get("statut") in ("NON VALIDE", "SANS DEPOT"):
        texte += " L'opération n'est pas validée dans le CRM."
    if ecart_montant:
        texte += " Même numéro de chèque, mais montant différent."
    return texte


def _choisir_etats(etats: list[dict], etat_id: str | None) -> list[dict]:
    if etat_id:
        choisis = [e for e in etats if e["id"] == etat_id]
        if not choisis:
            raise FichierInvalide("État introuvable dans le fichier.")
        return choisis
    derniers: dict[str, dict] = {}
    for e in etats:  # le dernier état de chaque feuille (le plus récent)
        actuel = derniers.get(e["feuille"])
        if actuel is None or (e["annee"], e["mois"]) >= (actuel["annee"], actuel["mois"]):
            derniers[e["feuille"]] = e
    return list(derniers.values())


def comparer(contenu: bytes, tolerance: int = 5, etat_id: str | None = None) -> dict:
    """Cherche chaque ligne de l'état dans les versements et chèques du CRM."""
    etats = lire_fichier(contenu)
    choisis = _choisir_etats(etats, etat_id)
    ids_choisis = {e["id"] for e in choisis}

    # 1. Périodes à lire dans la base : uniquement autour des lignes recherchées.
    fenetres = []
    for e in choisis:
        for l in e["lignes"]:
            l["fenetre"] = _fenetre(l, tolerance) if l["type"] in TYPES_RECHERCHES else None
            if l["fenetre"]:
                fenetres.append(l["fenetre"])
    periodes = _fusionner(fenetres)
    if len(periodes) > NB_PERIODES_MAX:
        raise FichierInvalide(
            "Cet état couvre trop de périodes différentes pour être comparé en une fois."
        )

    operations = []
    for debut, fin in periodes:
        for op in service.rapprochement(debut, fin)["operations"]:
            if op["statut"] == "ANNULE" or not op["date_operation"]:
                continue
            op = dict(op)
            op["_date"] = date.fromisoformat(str(op["date_operation"])[:10])
            op["_genre"] = "VERSEMENT" if op["type_operation"] == "VERSEMENT ESPECES" else "CHEQUE"
            operations.append(op)

    # 2. Rapprochement ligne par ligne : une opération du CRM ne sert qu'une fois.
    utilisees: set[int] = set()
    sortie = []
    for e in choisis:
        ordre = sorted(range(len(e["lignes"])), key=lambda i: (e["lignes"][i]["date"] or date.max, i))
        resultats: dict[int, tuple] = {}
        for i in ordre:
            l = e["lignes"][i]
            if l["type"] not in TYPES_RECHERCHES:
                resultats[i] = ("HORS_PERIMETRE", None, False)
                continue
            if not l["fenetre"]:
                resultats[i] = ("NON_TROUVE", None, False)
                continue
            debut, fin = l["fenetre"]
            candidats = [
                (k, op) for k, op in enumerate(operations)
                if k not in utilisees and op["_genre"] == l["type"] and debut <= op["_date"] <= fin
            ]
            par_numero = [
                (k, op) for k, op in candidats
                if l["num_cheque"] and op.get("num_cheque") and str(op["num_cheque"]).strip() == l["num_cheque"]
            ]
            par_montant = [
                (k, op) for k, op in candidats
                if abs((op["montant"] or 0) - (l["montant"] or 0)) < 0.01
            ]
            retenus = par_numero or par_montant
            if not retenus:
                resultats[i] = ("NON_TROUVE", None, False)
                continue
            repere = l["date"] or debut + (fin - debut) / 2

            def rang(element):
                _, op = element
                meme = _meme_compte(e["compte"], op.get("compte"))
                return (
                    0 if abs((op["montant"] or 0) - (l["montant"] or 0)) < 0.01 else 1,
                    0 if meme is not False else 1,
                    0 if op["statut"] in ("VALIDE", "DEPOSE") else 1,
                    abs((op["_date"] - repere).days),
                )

            k, op = min(retenus, key=rang)
            utilisees.add(k)
            ecart_montant = abs((op["montant"] or 0) - (l["montant"] or 0)) >= 0.01
            code = "TROUVE_AUTRE_COMPTE" if _meme_compte(e["compte"], op.get("compte")) is False else "TROUVE"
            resultats[i] = (code, op, ecart_montant)

        for i, l in enumerate(e["lignes"]):
            code, op, ecart_montant = resultats[i]
            mois = l["mois_origine"]
            sortie.append({
                "etat": e["id"],
                "banque_etat": e["banque"],
                "mois_etat": f"{e['mois']:02d}/{e['annee']}",
                "compte_etat": e["compte"],
                "cote": l["cote"],
                "cote_libelle": COTES[l["cote"]],
                "mois_origine": f"{mois[0]:02d}/{mois[1]}" if mois else None,
                "date": l["date"].isoformat() if l["date"] else None,
                "piece": l["piece"],
                "libelle": l["libelle"],
                "type": l["type"],
                "type_libelle": TYPES[l["type"]],
                "num_cheque": l["num_cheque"],
                "tiers": l["tiers"],
                "reference": l["reference"],
                "debit": l["debit"],
                "credit": l["credit"],
                "montant": l["montant"],
                "resultat_code": code,
                "resultat": RESULTATS[code],
                "lecture": _lecture(l["cote"], code, op, ecart_montant),
                "crm_date": op["date_operation"] if op else None,
                "ecart_jours": (op["_date"] - l["date"]).days if op and l["date"] else None,
                "crm_centre": op["centre"] if op else None,
                "crm_nom_centre": op["nom_centre"] if op else None,
                "crm_matricule": op["matricule"] if op else None,
                "crm_num_piece": op["num_piece"] if op else None,
                "crm_num_cheque": op.get("num_cheque") if op else None,
                "crm_banque": op["nom_banque"] if op else None,
                "crm_compte": op["compte"] if op else None,
                "crm_statut": op["statut"] if op else None,
                "crm_montant": op["montant"] if op else None,
            })

    # 3. Synthèse avec les parts dans l'ensemble.
    nb_total = len(sortie)
    montant_total = sum(l["montant"] or 0 for l in sortie)
    synthese = []
    for code, libelle in RESULTATS.items():
        lignes = [l for l in sortie if l["resultat_code"] == code]
        montant = sum(l["montant"] or 0 for l in lignes)
        synthese.append({
            "code": code,
            "libelle": libelle,
            "nb": len(lignes),
            "pct_nb": round(100 * len(lignes) / nb_total, 1) if nb_total else None,
            "montant": round(montant, 3),
            "pct_montant": round(100 * montant / montant_total, 1) if montant_total else None,
        })

    return {
        "etats": [
            {
                "id": e["id"],
                "feuille": e["feuille"],
                "banque": e["banque"],
                "mois": f"{e['mois']:02d}/{e['annee']}",
                "compte": e["compte"],
                "nb_lignes": len(e["lignes"]),
                "lecture_conforme": e["lecture_conforme"],
                "selectionne": e["id"] in ids_choisis,
            }
            for e in etats
        ],
        "tolerance_jours": tolerance,
        "nb_periodes_lues": len(periodes),
        "nb_lignes": nb_total,
        "montant_total": round(montant_total, 3),
        "synthese": synthese,
        "lignes": sortie,
    }
