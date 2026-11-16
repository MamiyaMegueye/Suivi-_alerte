"""Objectifs mensuels d'encaissement par centre (fichier « ObjR » de la DSP).

Le même objectif vaut pour chaque mois. Un centre sans objectif dans le
fichier a None.
"""
from __future__ import annotations

# code du centre -> (zone du fichier, nom du centre, objectif du mois en MRU)
OBJECTIFS: dict[str, tuple[str, str, float | None]] = {
    "43": ("Nouakchott - Zone Ouest", "TVZ SUD", 5518590.0),
    "55": ("Nouakchott - Zone Ouest", "TVZ NORD 3", 6048850.5),
    "91": ("Nouakchott - Zone Ouest", "TVZ NORD 1", 8800000.0),
    "92": ("Nouakchott - Zone Ouest", "CAPITALE", 6775050.28),
    "93": ("Nouakchott - Zone Ouest", "SEBKHA", 3243240.0),
    "97": ("Nouakchott - Zone Ouest", "TVZ NORD 2", 7154840.0),
    "42": ("Nouakchott - Zone Sud", "CARREFOUR 2", 2325840.0),
    "45": ("Nouakchott - Zone Sud", "MELLAH 1", 2435382.75),
    "46": ("Nouakchott - Zone Sud", "MELLAH 2", 2038168.0),
    "49": ("Nouakchott - Zone Sud", "TARHIL 1", 1985944.75),
    "50": ("Nouakchott - Zone Sud", "TARHIL 2", 1475820.87),
    "51": ("Nouakchott - Zone Sud", "TARHIL 3", 1182654.0),
    "53": ("Nouakchott - Zone Sud", "RIYADH 2", 1887392.77),
    "89": ("Nouakchott - Zone Sud", "RIYAD 1", 2300000.0),
    "90": ("Nouakchott - Zone Sud", "ARAFAT", 3500000.0),
    "94": ("Nouakchott - Zone Sud", "EL MINA", 6136680.0),
    "56": ("Nouakchott - Zone Sud", "EL MINA 2", None),
    "95": ("Nouakchott - Zone Sud", "CARREFOUR 1", 3119028.29),
    "41": ("Nouakchott - Zone Nord", "TEYARETT 2", 2960248.3),
    "44": ("Nouakchott - Zone Nord", "DAR NAIM-NORD", 1800000.0),
    "47": ("Nouakchott - Zone Nord", "TEYARETT 1", 2940977.4),
    "48": ("Nouakchott - Zone Nord", "DAR NAIM SUD 2", 2427021.0),
    "52": ("Nouakchott - Zone Nord", "MELLAH 3", 1832107.1),
    "86": ("Nouakchott - Zone Nord", "WAD NAGA", 1561340.0),
    "96": ("Nouakchott - Zone Nord", "KSAR", 4358131.7),
    "98": ("Nouakchott - Zone Nord", "TOUJOUNINE", 3300000.0),
    "57": ("Nouakchott - Zone Nord", "TENWEICH", None),
    "99": ("Nouakchott - Zone Nord", "DAR NAIM-SUD 1", 1800000.0),
    "40": ("Nouadhibou", "NOUADHIBOU 3", 2594218.12),
    "60": ("Nouadhibou", "NOUADHIBOU 1", 5894225.76),
    "80": ("Nouadhibou", "NOUADHIBOU 2", 3683988.0),
    "59": ("Nouadhibou", "BOULENOIR", None),
    "15": ("Nouadhibou", "CHAMI", 340000.0),
    "32": ("Interieur - Les Deux Hodhs", "TAMCHAKET", 250000.0),
    "33": ("Interieur - Les Deux Hodhs", "OULATA", 147528.0),
    "34": ("Interieur - Les Deux Hodhs", "AMOURJ", 220000.0),
    "35": ("Interieur - Les Deux Hodhs", "ADEL BAGROU", 570000.0),
    "37": ("Interieur - Les Deux Hodhs", "VASALA", 380000.0),
    "38": ("Interieur - Les Deux Hodhs", "TERMISSE", 80000.0),
    "70": ("Interieur - Les Deux Hodhs", "AIOUN EL ATROUSS", 1110000.0),
    "71": ("Interieur - Les Deux Hodhs", "NEMA", 2013880.1),
    "72": ("Interieur - Les Deux Hodhs", "TIMBEDRA", 1090000.0),
    "81": ("Interieur - Les Deux Hodhs", "DJIGUENI", 147309.85),
    "83": ("Interieur - Les Deux Hodhs", "KOUBENNI", 300000.0),
    "84": ("Interieur - Les Deux Hodhs", "BASSIKNOU", 690000.0),
    "85": ("Interieur - Les Deux Hodhs", "TINTANE NOUVEAU", 600000.0),
    "88": ("Interieur - Les Deux Hodhs", "N'BEIKETTE LEHWACH", 70000.0),
    "101": ("Interieur - Les Deux Hodhs", "AIWNAT ZBEL", 230000.0),
    "102": ("Interieur - Les Deux Hodhs", "TOUIL", 167073.0),
    "30": ("Interieur - Zone centre (Assaba et Tagant)", "BARKEOL", 200000.0),
    "31": ("Interieur - Zone centre (Assaba et Tagant)", "BOUMDEID", 98000.0),
    "39": ("Interieur - Zone centre (Assaba et Tagant)", "KIFFA 2", 820000.0),
    "73": ("Interieur - Zone centre (Assaba et Tagant)", "KIFFA", 1180000.0),
    "74": ("Interieur - Zone centre (Assaba et Tagant)", "GUEROU", 515000.0),
    "82": ("Interieur - Zone centre (Assaba et Tagant)", "KANKOUSSA", 400000.0),
    "19": ("Interieur - Zone centre (Assaba et Tagant)", "MOUDJERIA", 104652.0),
    "20": ("Interieur - Zone centre (Assaba et Tagant)", "TICHIT", 59797.5),
    "78": ("Interieur - Zone centre (Assaba et Tagant)", "TIDJIKJA", 734185.4),
    "14": ("Interieur - Zone Nord", "BENICHAB", 219338.0),
    "16": ("Interieur - Zone Nord", "AOUJEFT", 226299.0),
    "17": ("Interieur - Zone Nord", "CHINGUITT", 250000.0),
    "18": ("Interieur - Zone Nord", "OUADANE", 118357.0),
    "21": ("Interieur - Zone Nord", "FDEIRICK", 76201.0),
    "62": ("Interieur - Zone Nord", "AKJOUJT", 578451.8),
    "66": ("Interieur - Zone Nord", "ATAR", 1157195.75),
    "27": ("Interieur - Zone Sud", "MAGHAMA", 228198.6),
    "28": ("Interieur - Zone Sud", "MONGUEL", 350000.0),
    "29": ("Interieur - Zone Sud", "OULD YENGE", 290000.0),
    "65": ("Interieur - Zone Sud", "KAEDI", 1770116.25),
    "75": ("Interieur - Zone Sud", "SELIBABY", 1400000.0),
    "76": ("Interieur - Zone Sud", "M'BOUT", 460000.0),
    "105": ("Interieur - Zone Sud", "LEXEIBA", 481617.1),
    "106": ("Interieur - Zone Sud", "WOMPOU", 62517.0),
    "107": ("Interieur - Zone Sud", "GABOU", 132346.0),
    "23": ("Interieur - Zone Trarza - Brakna", "RKIZ", 550611.05),
    "24": ("Interieur - Zone Trarza - Brakna", "KEUR MACENE", 96000.0),
    "64": ("Interieur - Zone Trarza - Brakna", "ROSSO", 1350000.0),
    "68": ("Interieur - Zone Trarza - Brakna", "BOUTILIMIT", 1227902.8),
    "69": ("Interieur - Zone Trarza - Brakna", "MEDERDRA", 412045.05),
    "25": ("Interieur - Zone Trarza - Brakna", "BABABE", 305588.0),
    "26": ("Interieur - Zone Trarza - Brakna", "M'BAGNE", 100000.0),
    "36": ("Interieur - Zone Trarza - Brakna", "SANGRAVA", 127207.8),
    "61": ("Interieur - Zone Trarza - Brakna", "BOGHE", 650519.1),
    "67": ("Interieur - Zone Trarza - Brakna", "ALEG", 1018834.9),
    "77": ("Interieur - Zone Trarza - Brakna", "MAGTALAHAJAR", 741632.0),
    "87": ("Interieur - Zone Trarza - Brakna", "CHOGGAR", 600000.0),
    "103": ("Interieur - Zone Trarza - Brakna", "BOUHDIDA", 300000.0),
    "104": ("Interieur - Zone Trarza - Brakna", "MAAL", 106062.0),
    "108": ("Interieur - Zone Trarza - Brakna", "TEKANE", 60136.2),
}


# --------------------------------------------------------------------------- modèle
# Objectifs calculés par le modèle multiniveau (backend/modele_objectifs).
# Pour un mois donné, si le fichier sorties/objectifs_AAAA-MM.csv existe, ses
# objectifs remplacent ceux de la DSP ; sinon les objectifs DSP ci-dessus servent.
import csv as _csv
from pathlib import Path as _Path

_SORTIES_MODELE = _Path(__file__).resolve().parents[2] / "modele_objectifs" / "sorties"
# L'écran compte El Mina 2 avec El Mina 1 : leurs objectifs sont additionnés.
_FUSION_ECRAN = {"56": "94"}


def pour_mois(mois: str) -> tuple[dict[str, tuple[str | None, str | None, float | None]], str]:
    """Objectifs du mois (AAAA-MM) et leur source : « Modèle » ou « DSP »."""
    chemin = _SORTIES_MODELE / f"objectifs_{mois}.csv"
    if not chemin.exists():
        return OBJECTIFS, "DSP"
    table: dict[str, tuple[str | None, str | None, float | None]] = {}
    try:
        with open(chemin, encoding="utf-8-sig", newline="") as f:
            for ligne in _csv.DictReader(f, delimiter=";"):
                code = str(ligne.get("centre", "")).strip()
                try:
                    valeur = float(str(ligne.get("objectif", "")).replace(",", "."))
                except ValueError:
                    continue
                code = _FUSION_ECRAN.get(code, code)
                zone, nom, _ = OBJECTIFS.get(code, (None, ligne.get("nom_centre") or None, None))
                deja = (table.get(code) or (zone, nom, 0.0))[2] or 0.0
                table[code] = (zone, nom, deja + valeur)
    except OSError:
        return OBJECTIFS, "DSP"
    return (table, "Modèle") if table else (OBJECTIFS, "DSP")
