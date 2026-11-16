"""Évolution mensuelle des encaissements par centre.

Chaque centre est comparé, pour un mois choisi, au mois précédent et à la
moyenne des trois mois précédents. Un mois en cours est comparé « à date » :
du 1er au même jour dans chacun des mois précédents, pour ne pas prendre un
mois incomplet pour une chute.
"""
from __future__ import annotations

import calendar
from datetime import date

# Seuils de variation par rapport au mois précédent, en %.
SEUIL_CHUTE = -30.0
SEUIL_BAISSE = -10.0
SEUIL_HAUSSE = 10.0

CHUTE = "EVO_CHUTE"
ARRET = "EVO_ARRET"
BAISSE = "EVO_BAISSE"
STABLE = "EVO_STABLE"
HAUSSE = "EVO_HAUSSE"
NOUVEAU = "EVO_NOUVEAU"

LIBELLES = {
    ARRET: "Plus aucun encaissement",
    CHUTE: f"Chute forte (≤ {SEUIL_CHUTE:.0f} %)",
    BAISSE: f"Baisse ({SEUIL_CHUTE:.0f} à {SEUIL_BAISSE:.0f} %)",
    STABLE: f"Stable ({SEUIL_BAISSE:.0f} à +{SEUIL_HAUSSE:.0f} %)",
    HAUSSE: f"Hausse (≥ +{SEUIL_HAUSSE:.0f} %)",
    NOUVEAU: "Nouveau (rien le mois précédent)",
}
ORDRE = (ARRET, CHUTE, BAISSE, STABLE, HAUSSE, NOUVEAU)

NOMS_MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre")


def mois_de(texte: str) -> tuple[int, int]:
    a, m = texte.split("-")[:2]
    return int(a), int(m)


def decaler(annee: int, mois: int, n: int) -> tuple[int, int]:
    """Mois décalé de n mois (n négatif : en arrière)."""
    k = annee * 12 + (mois - 1) + n
    return k // 12, k % 12 + 1


def cle(annee: int, mois: int) -> str:
    return f"{annee:04d}-{mois:02d}"


def libelle(annee: int, mois: int) -> str:
    return f"{NOMS_MOIS[mois - 1]} {annee}"


def bornes(annee: int, mois: int, jour_max: int | None = None) -> tuple[date, date]:
    """Du 1er au dernier jour du mois, ou au jour `jour_max` s'il est donné."""
    dernier = calendar.monthrange(annee, mois)[1]
    return date(annee, mois, 1), date(annee, mois, min(dernier, jour_max or dernier))


def _pct(part: float, total: float) -> float | None:
    return round(100.0 * part / total, 1) + 0.0 if total else None


def _variation(actuel: float, avant: float) -> float | None:
    return round(100.0 * (actuel - avant) / avant, 1) + 0.0 if avant else None


def resultat(actuel: float, avant: float) -> str:
    if avant <= 0:
        return NOUVEAU
    if actuel <= 0:
        return ARRET
    v = 100.0 * (actuel - avant) / avant
    if v <= SEUIL_CHUTE:
        return CHUTE
    if v <= SEUIL_BAISSE:
        return BAISSE
    if v < SEUIL_HAUSSE:
        return STABLE
    return HAUSSE


def comparer(actuel: dict[str, tuple[int, float]], precedent: dict[str, tuple[int, float]],
             avant: list[dict[str, tuple[int, float]]], noms: dict[str, str]) -> list[dict]:
    """Une ligne par centre : mois choisi, mois précédent, moyenne des mois d'avant.

    Chaque dictionnaire donne, par centre, (nombre de reçus, montant)."""
    total = sum(m for _, m in actuel.values())
    total_prec = sum(m for _, m in precedent.values())
    centres = {c for c, (_, m) in actuel.items() if m} | {c for c, (_, m) in precedent.items() if m}
    lignes = []
    for c in centres:
        nb, m = actuel.get(c, (0, 0.0))
        nb_p, m_p = precedent.get(c, (0, 0.0))
        anciens = [d.get(c, (0, 0.0))[1] for d in avant]
        moyenne = sum(anciens) / len(anciens) if anciens else None
        code = resultat(m, m_p)
        lignes.append({
            "centre": c, "nom_centre": noms.get(c),
            "encaisse": round(m, 3), "encaisse_precedent": round(m_p, 3),
            "ecart": round(m - m_p, 3), "variation_pct": _variation(m, m_p),
            "moyenne_3_mois": round(moyenne, 3) if moyenne is not None else None,
            "variation_moyenne_pct": _variation(m, moyenne) if moyenne else None,
            "part_pct": _pct(m, total), "part_precedente_pct": _pct(m_p, total_prec),
            "nb_recus": nb, "nb_recus_precedent": nb_p,
            "resultat_code": code, "resultat": LIBELLES[code],
        })
    gravite = {k: i for i, k in enumerate(ORDRE)}
    lignes.sort(key=lambda l: (gravite[l["resultat_code"]], l["variation_pct"] if l["variation_pct"] is not None else 0,
                               -l["encaisse_precedent"]))
    return lignes


def resumer(lignes: list[dict]) -> dict:
    nb = len(lignes)
    total = sum(l["encaisse"] for l in lignes)
    total_prec = sum(l["encaisse_precedent"] for l in lignes)
    synthese = []
    for code in ORDRE:
        choisies = [l for l in lignes if l["resultat_code"] == code]
        if not choisies:
            continue
        ecart = sum(l["ecart"] for l in choisies)
        synthese.append({
            "code": code, "libelle": LIBELLES[code], "nb": len(choisies), "pct_nb": _pct(len(choisies), nb),
            "encaisse": round(sum(l["encaisse"] for l in choisies), 3),
            "encaisse_precedent": round(sum(l["encaisse_precedent"] for l in choisies), 3),
            "ecart": round(ecart, 3),
        })
    pertes = sum(l["ecart"] for l in lignes if l["ecart"] < 0)
    return {
        "synthese": synthese,
        "totaux": {
            "nb_centres": nb, "encaisse": round(total, 3), "encaisse_precedent": round(total_prec, 3),
            "ecart": round(total - total_prec, 3), "variation_pct": _variation(total, total_prec),
            "nb_recus": sum(l["nb_recus"] for l in lignes),
            "nb_recus_precedent": sum(l["nb_recus_precedent"] for l in lignes),
            "nb_en_baisse": sum(1 for l in lignes if l["resultat_code"] in (ARRET, CHUTE, BAISSE)),
            "pct_en_baisse": _pct(sum(1 for l in lignes if l["resultat_code"] in (ARRET, CHUTE, BAISSE)), nb),
            "pertes": round(pertes, 3),
        },
    }


def historique(par_mois: dict[tuple[str, str], tuple[int, float]], mois: list[str],
               centres: list[str], noms: dict[str, str]) -> dict:
    """Montant de chaque mois, par centre et au total, avec la variation sur le mois d'avant."""
    def serie(valeurs):
        sortie, avant = [], None
        for k, (nb, m) in zip(mois, valeurs):
            sortie.append({"mois": k, "encaisse": round(m, 3), "nb_recus": nb,
                           "variation_pct": _variation(m, avant) if avant is not None else None})
            avant = m
        return sortie

    total = [(sum(par_mois.get((c, k), (0, 0.0))[0] for c in centres),
              sum(par_mois.get((c, k), (0, 0.0))[1] for c in centres)) for k in mois]
    return {
        "mois": [{"mois": k, "libelle": libelle(*mois_de(k))} for k in mois],
        "total": serie(total),
        "centres": {c: {"nom_centre": noms.get(c), "valeurs": serie([par_mois.get((c, k), (0, 0.0)) for k in mois])}
                    for c in centres},
    }


NOMS_COURTS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")


def annee(par_mois: dict[tuple[str, str], tuple[int, float]], annee_: int, dernier_mois: int,
          a_date: dict[str, float] | None, total_a_date: float | None, noms: dict[str, str],
          centres_choisis: list[str] | None = None) -> dict:
    """Une ligne par centre : l'encaissé de chaque mois de l'année et sa
    variation sur le mois précédent (décembre de l'année d'avant pour janvier).

    `a_date` : pour un mois en cours, l'encaissé du mois précédent arrêté au
    même jour, par centre (la variation du mois en cours se calcule dessus).
    L'évolution sur l'année compare le dernier mois complet à janvier."""
    mois = [cle(annee_, m) for m in range(1, dernier_mois + 1)]
    decembre = cle(annee_ - 1, 12)
    en_cours = a_date is not None
    complets = mois[:-1] if en_cours else mois

    def ligne(valeur_de, precedent_a_date):
        valeurs, avant = [], valeur_de(decembre)
        for i, k in enumerate(mois):
            v = valeur_de(k)
            base = precedent_a_date if (en_cours and i == len(mois) - 1) else avant
            valeurs.append({"mois": k, "encaisse": round(v, 3), "precedent": round(base, 3) if base is not None else None,
                            "variation_pct": _variation(v, base) if base else None})
            avant = v
        premier = valeur_de(mois[0]) if mois else 0.0
        dernier = valeur_de(complets[-1]) if len(complets) > 1 else None
        return {
            "valeurs": valeurs,
            "total": round(sum(x["encaisse"] for x in valeurs), 3),
            "evolution_pct": _variation(dernier, premier) if dernier is not None and premier else None,
        }

    codes = centres_choisis if centres_choisis is not None else sorted(
        {c for c, k in par_mois if k in mois and par_mois[(c, k)][1]},
        key=lambda c: (0, int(c), "") if c.isdigit() else (1, 0, c))
    lignes = []
    for c in codes:
        r = ligne(lambda k: par_mois.get((c, k), (0, 0.0))[1], (a_date or {}).get(c, 0.0))
        lignes.append({"centre": c, "nom_centre": noms.get(c), **r})
    tous = sorted({c for c, _ in par_mois})
    total = ligne(lambda k: sum(par_mois.get((c, k), (0, 0.0))[1] for c in tous), total_a_date or 0.0)
    return {
        "annee": annee_,
        "mois": [{"mois": k, "libelle": NOMS_COURTS[int(k[5:]) - 1]} for k in mois],
        "dernier_complet": complets[-1] if len(complets) > 1 else None,
        "lignes": lignes,
        "total": total,
    }
