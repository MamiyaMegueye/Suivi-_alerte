"""
Logique des cycles de relève bimestriels.

La relève SNDE est bimestrielle : un secteur relevé en septembre l'est de
nouveau en novembre, jamais en octobre. Les secteurs se répartissent donc en
deux groupes de parité :

    groupe impair : ... 07 → 09 → 11 ...
    groupe pair   : ... 08 → 10 → 12 ...

Comparer le cycle 09/2026 au cycle 08/2026 n'a aucun sens : ce ne sont pas
les mêmes secteurs. Le cycle de comparaison est toujours **N-2**, c'est-à-dire
le cycle précédent de MÊME PARITÉ. C'est la règle déjà appliquée par le
dashboard (colonne `ref_mois` de v_secteur_cycle).

Un cycle est identifié par sa clé entière `cycle_key = ANNEE * 100 + MOIS`.
"""

from __future__ import annotations


def cycle_key(annee: int, mois: int) -> int:
    """202609 pour (2026, 9)."""
    return annee * 100 + mois


def depuis_key(key: int) -> tuple[int, int]:
    """(2026, 9) pour 202609."""
    return key // 100, key % 100


def cycle_precedent(annee: int, mois: int) -> tuple[int, int]:
    """Cycle N-2 : deux mois en arrière, avec passage d'année.

    >>> cycle_precedent(2026, 9)
    (2026, 7)
    >>> cycle_precedent(2026, 2)
    (2025, 12)
    >>> cycle_precedent(2026, 1)
    (2025, 11)
    """
    m = mois - 2
    a = annee
    if m <= 0:
        m += 12
        a -= 1
    return a, m


def mois_precedent(annee: int, mois: int) -> tuple[int, int]:
    """Cycle N-1 : le mois précédent. Sert aux gros consommateurs,
    facturés tous les mois.

    >>> mois_precedent(2026, 1)
    (2025, 12)
    """
    return (annee - 1, 12) if mois == 1 else (annee, mois - 1)


def cycle_precedent_key(key: int) -> int:
    """202607 pour 202609."""
    a, m = cycle_precedent(*depuis_key(key))
    return cycle_key(a, m)


def parite(mois: int) -> str:
    """Groupe de relève du mois : 'pair' ou 'impair'."""
    return "pair" if mois % 2 == 0 else "impair"


def libelle(annee: int, mois: int) -> str:
    """'09/2026'."""
    return f"{mois:02d}/{annee}"


def libelle_key(key: int) -> str:
    """'09/2026' pour 202609."""
    return libelle(*depuis_key(key))
