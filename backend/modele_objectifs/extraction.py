"""
EXTRACTION DES DONNÉES DU MODÈLE
================================

Utilise la connexion du projet (backend/.env) et sa base locale DuckDB :

- facturation.csv  : CA par centre, mois et tarif     -> Oracle, S_FACTURE_M (groupements compris)
- abonnes.csv      : abonnés actifs par centre         -> Oracle, S_ABONNEMENT
- encaissements.csv: encaissé validé par centre et mois -> mêmes règles que l'écran
                     des encaissements (centre de l'abonné, e-paiements compris)
- zones.csv        : zone de chaque centre             -> src/api/zones.py
- objectifs_actuels.csv : objectifs DSP actuels        -> src/api/objectifs.py

Le backend (uvicorn) doit être arrêté : DuckDB n'accepte qu'un programme à la fois.
"""
import csv
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ICI = Path(__file__).resolve().parent
BACKEND = ICI.parent
sys.path.insert(0, str(BACKEND))

import parametres as C                                    # noqa: E402
from src.api import db, objectifs, service, store, zones  # noqa: E402
from src.api.config import get_settings                    # noqa: E402

D_IN = ICI / C.DOSSIER_DONNEES
D_IN.mkdir(exist_ok=True)

DELAI_ORACLE_MS = 900_000  # 15 min par requête au maximum


# -------------------------------------------------------------
def _mois_de_la_periode(debut=None):
    a, m = map(int, (debut or C.MOIS_DEBUT).split("-"))
    fin = tuple(map(int, C.MOIS_FIN.split("-")))
    while (a, m) <= fin:
        d1 = date(a, m, 1)
        d2 = date(a + (m == 12), m % 12 + 1, 1)
        yield f"{a:04d}-{m:02d}", d1, d2
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)


def _bornes(debut=None):
    mois = list(_mois_de_la_periode(debut))
    return mois[0][1], mois[-1][2] - timedelta(days=1)


def _centre(code) -> str:
    code = str(code).strip()
    return C.FUSION_CENTRES.get(code, code)


def _ecrire(nom, colonnes, lignes):
    chemin = D_IN / nom
    with open(chemin, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(colonnes)
        w.writerows(lignes)
    print(f"  ✅ {nom} : {len(lignes)} lignes")


def _liste_tarifs():
    # Tarifs gardés à part dans l'extraction : gros consommateurs et tarifs mensuels
    tarifs = list(dict.fromkeys([str(t).strip() for t in
                                 list(C.TARIFS_GC) + list(getattr(C, "TARIFS_FACTURATION_MENSUELLE", []))]))
    for t in tarifs:
        if not re.fullmatch(r"[A-Za-z0-9]+", t):
            sys.exit(f"❌ Tarif invalide dans parametres.TARIFS_GC : {t!r}")
    return ", ".join(f"'{t}'" for t in tarifs)


# -------------------------------------------------------------
# 1. FACTURATION (Oracle)
# -------------------------------------------------------------
def sql_facturation(S: str) -> str:
    gc = _liste_tarifs()
    return f"""
WITH tarif_abn AS (
    SELECT a.ABN_ID, COALESCE(t.TARP_CODE, u.TARIFA) AS tarif
    FROM {S}.S_ABONNEMENT a
    LEFT JOIN (SELECT PFACT_ID, MIN(TARP_CODE) AS TARP_CODE
               FROM {S}.S_TARIF_PR GROUP BY PFACT_ID) t ON t.PFACT_ID = a.PFACT_ID
    LEFT JOIN {S}.ACT_USAGE u ON u.ACTUSAGE_ID = a.ACTUSAGE_ID
)
SELECT TRIM(s.STR_CODE)                                         AS centre,
       MAX(s.STR_LIB_LT)                                        AS nom_centre,
       CASE WHEN TRIM(t.tarif) IN ({gc}) THEN TRIM(t.tarif) ELSE 'AUTRE' END AS groupe_tarif,
       COUNT(DISTINCT m.ABN_ID)                                 AS nb_abonnes,
       COUNT(*)                                                 AS nb_factures,
       SUM(m.FACT_MNT)                                          AS ca
FROM {S}.S_FACTURE_M m
JOIN {S}.S_STRUCTURE s ON s.STR_ID = m.STR_ID
LEFT JOIN tarif_abn t  ON t.ABN_ID = m.ABN_ID
WHERE m.FACT_DATE >= :d1
  AND m.FACT_DATE <  :d2
  AND NVL(m.FLAGANNULLE, 0) = 0
GROUP BY TRIM(s.STR_CODE),
         CASE WHEN TRIM(t.tarif) IN ({gc}) THEN TRIM(t.tarif) ELSE 'AUTRE' END
"""


def extraire_facturation(S):
    print("\n[Oracle] Facturation (S_FACTURE_M : une facture par abonné, groupements compris), mois par mois")
    sql = sql_facturation(S)
    agg = {}
    for mois, d1, d2 in _mois_de_la_periode(getattr(C, "MOIS_DEBUT_FACTURATION", None)):
        t0 = time.time()
        lignes = db.fetch_all(sql, {"d1": d1, "d2": d2}, delai_ms=DELAI_ORACLE_MS)
        for l in lignes:
            cle = (_centre(l["centre"]), mois, l["groupe_tarif"])
            nom, nb_a, nb_f, ca = agg.get(cle, ("", 0, 0, 0.0))
            agg[cle] = (nom or l["nom_centre"] or "", nb_a + int(l["nb_abonnes"] or 0),
                        nb_f + int(l["nb_factures"] or 0), ca + float(l["ca"] or 0))
        total = sum(float(l["ca"] or 0) for l in lignes)
        print(f"   {mois} : {len(lignes):4d} lignes, CA = {total:>16,.0f}  ({time.time() - t0:.0f} s)")
    _ecrire(C.FICHIERS["facturation"],
            ["centre", "nom_centre", "mois", "groupe_tarif", "nb_abonnes", "nb_factures", "ca"],
            [(c, v[0], m, g, v[1], v[2], round(v[3], 2)) for (c, m, g), v in sorted(agg.items())])


# -------------------------------------------------------------
# 2. ABONNÉS ACTIFS (Oracle)
# -------------------------------------------------------------
def extraire_abonnes(S):
    print("\n[Oracle] Abonnés actifs et gros consommateurs actifs (S_ABONNEMENT)")
    gc = ", ".join(f"'{t}'" for t in [str(x).strip() for x in C.TARIFS_GC])
    lignes = db.fetch_all(f"""
SELECT TRIM(s.STR_CODE) AS centre,
       COUNT(*)         AS nb_abonnes_actifs,
       SUM(CASE WHEN TRIM(COALESCE(t.TARP_CODE, u.TARIFA)) IN ({gc}) THEN 1 ELSE 0 END) AS nb_gc_actifs
FROM {S}.S_ABONNEMENT a
JOIN {S}.S_STRUCTURE s ON s.STR_ID = a.STR_ID
LEFT JOIN (SELECT PFACT_ID, MIN(TARP_CODE) AS TARP_CODE
           FROM {S}.S_TARIF_PR GROUP BY PFACT_ID) t ON t.PFACT_ID = a.PFACT_ID
LEFT JOIN {S}.ACT_USAGE u ON u.ACTUSAGE_ID = a.ACTUSAGE_ID
WHERE NVL(a.ABN_RES, 0) = 0
  AND NVL(a.FLAG_SUS, 0) = 0
GROUP BY TRIM(s.STR_CODE)
""", delai_ms=DELAI_ORACLE_MS)
    agg = {}
    for l in lignes:
        c = _centre(l["centre"])
        n, g = agg.get(c, (0, 0))
        agg[c] = (n + int(l["nb_abonnes_actifs"] or 0), g + int(l["nb_gc_actifs"] or 0))
    print(f"   total : {sum(v[0] for v in agg.values()):,} abonnés actifs, "
          f"{sum(v[1] for v in agg.values()):,} gros consommateurs actifs")
    _ecrire(C.FICHIERS["abonnes"], ["centre", "nb_abonnes_actifs", "nb_gc_actifs"],
            [(c, v[0], v[1]) for c, v in sorted(agg.items())])


# -------------------------------------------------------------
# 3. ENCAISSEMENTS (base locale DuckDB, complétée depuis Oracle)
# -------------------------------------------------------------
def extraire_encaissements():
    debut, fin = _bornes(C.MOIS_DEBUT_ENCAISSEMENTS)
    print(f"\n[DuckDB] Encaissements validés du {debut} au {fin} "
          f"(les jours manquants sont chargés depuis Oracle, cela peut être long)")
    t0 = time.time()
    for essai in range(1, 13):
        try:
            with service.en_attente():
                service._assurer_encaissements(debut, fin)
            break
        except service.ChargementEnCours as exc:
            print(f"   … {exc} (essai {essai})")
    else:
        sys.exit("❌ Chargement des encaissements trop long : relancez plus tard.")

    def _par_mois(segment):
        valeurs, _ = store.encaisse_par_mois(debut, fin, segment)
        agg = {}
        for (c, mois), (_, montant) in valeurs.items():
            cle = (_centre(c), mois)
            agg[cle] = agg.get(cle, 0.0) + montant
        return agg

    tous, prives, etat = _par_mois(None), _par_mois("prives"), _par_mois("etat")
    print(f"   chargé en {time.time() - t0:.0f} s : total = {sum(tous.values()):,.0f} MRU "
          f"(privés {sum(prives.values()):,.0f}, État {sum(etat.values()):,.0f})")
    _ecrire(C.FICHIERS["encaissements"], ["centre", "mois", "montant", "montant_prives", "montant_etat"],
            [(c, m, round(v, 2), round(prives.get((c, m), 0.0), 2), round(etat.get((c, m), 0.0), 2))
             for (c, m), v in sorted(tous.items())])


# -------------------------------------------------------------
# 3 bis. STOCK D'ARRIÉRÉS (Oracle)
# -------------------------------------------------------------
def extraire_arrieres(S):
    """Arriérés de chaque abonné sur sa dernière facture (3 derniers mois), additionnés par centre."""
    print("\n[Oracle] Stock d'arriérés (dernière facture de chaque abonné et groupement, S_FACTURE_RECAP)")
    _, fin = _bornes()
    d2 = fin + timedelta(days=1)
    a, m = d2.year, d2.month
    k = a * 12 + (m - 1) - 3
    d0 = date(k // 12, k % 12 + 1, 1)
    lignes = db.fetch_all(f"""
SELECT TRIM(s.STR_CODE) AS centre,
       SUM(GREATEST(NVL(x.arr, 0), 0)) AS arrieres,
       COUNT(*) AS nb_comptes
FROM (
    SELECT r.STR_ID, r.FRCP_MNT_ARR AS arr,
           ROW_NUMBER() OVER (
               PARTITION BY NVL(TO_CHAR(r.ABN_ID), 'G' || TO_CHAR(r.GRP_ID))
               ORDER BY r.FRCP_DATE DESC, r.FRCP_ID DESC) AS rn
    FROM {S}.S_FACTURE_RECAP r
    WHERE r.FRCP_DATE >= :d0
      AND r.FRCP_DATE <  :d2
      AND NVL(r.FLAG_ANNUL, 0) = 0
      AND (r.ABN_ID IS NOT NULL OR r.GRP_ID IS NOT NULL)
) x
JOIN {S}.S_STRUCTURE s ON s.STR_ID = x.STR_ID
WHERE x.rn = 1
GROUP BY TRIM(s.STR_CODE)
""", {"d0": d0, "d2": d2}, delai_ms=DELAI_ORACLE_MS)
    agg = {}
    for l in lignes:
        c = _centre(l["centre"])
        agg[c] = agg.get(c, 0.0) + float(l["arrieres"] or 0)
    print(f"   du {d0} au {fin} : total = {sum(agg.values()):,.0f} MRU")
    _ecrire(C.FICHIERS["arrieres"], ["centre", "arrieres"], [(c, round(v, 2)) for c, v in sorted(agg.items())])


# -------------------------------------------------------------
# 4. ZONES ET OBJECTIFS ACTUELS (fichiers du projet)
# -------------------------------------------------------------
def _zone_modele(zone: str | None) -> str:
    z = (zone or "").upper()
    if z.startswith("NKTT") or z.startswith("NOUAKCHOTT"):
        return "NKTT"
    if z.startswith("NOUADHIBOU"):
        return "NDB"
    if z.startswith("INTERIEUR") or z.startswith("INTÉRIEUR"):
        return "INTERIEUR"
    return ""


def extraire_zones():
    print("\n[Projet] Zones (zones.py, complété par objectifs.py)")
    lignes = {}
    for code, (zone_obj, nom, _) in objectifs.OBJECTIFS.items():
        lignes[_centre(code)] = (nom, _zone_modele(zone_obj))
    for code, (zone, nom) in zones.CENTRES.items():
        lignes[_centre(code)] = (nom, _zone_modele(zone))
    _ecrire(C.FICHIERS["zones"], ["centre", "nom_centre", "zone"],
            [(c, n, z) for c, (n, z) in sorted(lignes.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0)])

    agg = {}
    for code, (_, _, obj) in objectifs.OBJECTIFS.items():
        if obj is not None:
            c = _centre(code)
            agg[c] = agg.get(c, 0.0) + float(obj)
    _ecrire(C.FICHIERS["objectifs_actuels"], ["centre", "objectif_actuel"], sorted(agg.items()))


# -------------------------------------------------------------
def main():
    s = get_settings()
    if not s.oracle_configure:
        sys.exit("❌ Connexion Oracle non configurée dans backend/.env")
    S = s.oracle_schema
    print("=" * 62)
    print(f"  EXTRACTION  {C.MOIS_DEBUT} → {C.MOIS_FIN}  (objectif de {C.MOIS_OBJECTIF})")
    print(f"  Oracle {s.dsn}, schéma {S}")
    print("=" * 62)
    try:
        extraire_zones()
        extraire_abonnes(S)
        extraire_facturation(S)
        extraire_arrieres(S)
        extraire_encaissements()
    except store.BaseLocaleIndisponible as exc:
        sys.exit(f"❌ {exc}\n   Arrêtez le backend (uvicorn) puis relancez.")
    except db.BaseIndisponible as exc:
        sys.exit(f"❌ {exc}")
    finally:
        try:
            service.arreter()
        except Exception:
            pass
        store.fermer()
        db.fermer_pool()
    print("\nExtraction terminée.")


if __name__ == "__main__":
    main()
