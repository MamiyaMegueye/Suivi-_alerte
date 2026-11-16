"""
Test de l'onglet « Gros consommateurs » sur une base DuckDB JETABLE.
Ne touche ni à data/baisse.duckdb ni à Oracle.

    .venv\\Scripts\\python.exe -m tests.test_gros_consommateurs

Données fabriquées (centre 10, 12 mois de 12/2025 à 11/2026) :
  201  VRAI MENSUEL   mêmes 12 abonnés tous les mois, 10 m³ chacun,
                      8 m³ en 11/2026      -> N-1 : 96 contre 120
  202  BIMESTRIEL     12 abonnés les mois impairs, 10 m³,
                      9 m³ en 11/2026      -> N-2 : 108 contre 120
  203  DEUX GROUPES   12 abonnés les mois impairs + 12 AUTRES les mois
                      pairs : facturé tous les mois, mais jamais deux fois
                      de suite pour le même abonné -> reste bimestriel
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp()) / "test_gros.duckdb"
os.environ["BAISSE_DB"] = str(_TMP)
os.environ["WATCHER_ACTIF"] = "0"

from src import alerts, config, db, queries  # noqa: E402

assert Path(config.DB_PATH) == _TMP, "garde-fou : mauvaise base"
ok = True


def verifier(nom, cond, detail=""):
    global ok
    print(("  OK    " if cond else "  ÉCHEC ") + nom
          + (f"  ({detail})" if detail and not cond else ""))
    ok &= bool(cond)


def mois_du_test():
    a, m = 2025, 12
    for _ in range(12):
        yield a, m
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)


def preparer():
    db.initialiser()
    db.executer("INSERT INTO ref_centre VALUES (10, 'CENTRE TEST', 2)")
    for sid in (201, 202, 203):
        db.executer("INSERT INTO ref_secteur VALUES (?, ?, ?, 10, '1')",
                    [sid, f"SECT {sid}", str(sid)])
    fid = [0]

    def facture(sect, abn, a, m, vol):
        fid[0] += 1
        db.executer(
            "INSERT INTO raw_facture_m (FACT_ID, FACT_NUM, STR_ID, SECT_ID, ABN_ID, "
            "FACT_ANNEE, FACT_PERIODE, TYPE_FACT, FAT_QTE_CONS, FLAGANNULLE) "
            "VALUES (?, ?, 10, ?, ?, ?, ?, 0, ?, 0)",
            [fid[0], fid[0], sect, abn, a, m, vol])

    for a, m in mois_du_test():
        dernier = (a, m) == (2026, 11)
        for i in range(12):
            facture(201, 2000 + i, a, m, 8 if dernier else 10)
            if m % 2 == 1:
                facture(202, 4000 + i, a, m, 9 if dernier else 10)
                facture(203, 3000 + i, a, m, 10)
            else:
                facture(203, 3100 + i, a, m, 10)

    idc = 0
    for sect, cycles_ in ((201, ((2026, 9), (2026, 10), (2026, 11))),
                          (202, ((2026, 9), (2026, 11))),
                          (203, ((2026, 9), (2026, 10), (2026, 11)))):
        for a, m in cycles_:
            idc += 1
            db.executer(
                "INSERT INTO raw_cloture_secteur (IDCLOTURE, STR_ID, SECT_ID, ANNEE, "
                "MOIS, NBABO, NBTRAITER, POURCTRAITER, VOLUMEFACT, VOLUMESTIM, CLOTURE) "
                "VALUES (?, 10, ?, ?, ?, 12, 12, 100, 0, 0, 1)", [idc, sect, a, m])


def tests():
    print("Repérage")
    mens = queries.secteurs_mensuels(2026, 11)
    verifier("seul le secteur 201 est gros consommateur", mens == {201}, str(mens))

    print("Onglet Gros consommateurs (N-1)")
    g = {r["secteur_code"]: r for r in queries.secteurs(2026, 11, population="mensuel")}
    verifier("ne contient que 201", set(g) == {201}, str(set(g)))
    if 201 in g:
        r = g[201]
        verifier("201 : 96 contre 120 m³, écart -24",
                 (r["vol_n"], r["vol_p"], r["ecart"]) == (96, 120, -24),
                 f"{r['vol_n']} / {r['vol_p']} / {r['ecart']}")
        verifier("201 comparé à N-1", r.get("comparaison") == "N-1", r.get("comparaison"))
    syn = queries.synthese(2026, 11, population="mensuel")
    verifier("synthèse : cycle de référence 10/2026",
             syn["cycle_precedent"] == "10/2026", syn["cycle_precedent"])

    print("Onglet Baisses (N-2)")
    b = {r["secteur_code"]: r for r in queries.secteurs(2026, 11, seulement_baisses=False)}
    verifier("201 n'y est plus", 201 not in b, str(set(b)))
    verifier("202 : 108 contre 120 m³", 202 in b and b[202]["ecart"] == -12,
             str(b.get(202, {}).get("ecart")))
    verifier("203 (deux groupes) reste ici, stable",
             203 in b and b[203]["ecart"] == 0, str(b.get(203, {}).get("ecart")))
    verifier("synthèse : référence 09/2026",
             queries.synthese(2026, 11)["cycle_precedent"] == "09/2026")

    print("Détail par abonné et avancement")
    d = queries.abonnes(2026, 11, 10, 201)
    verifier("détail 201 comparé au mois 10/2026",
             d.get("comparaison") == "N-1" and d["cycle_precedent"] == "10/2026",
             f"{d.get('comparaison')} {d['cycle_precedent']}")
    d2 = queries.abonnes(2026, 11, 10, 202)
    verifier("détail 202 comparé au cycle 09/2026",
             d2.get("comparaison") == "N-2" and d2["cycle_precedent"] == "09/2026")
    verifier("détail : uniquement des abonnés facturés",
             d["lignes"] and all(x["num_facture"] is not None for x in d["lignes"]),
             str(len(d["lignes"])))
    av = {r["secteur_code"]: r for r in queries.avancement(2026, 11)}
    verifier("avancement 201 : écart -24 à N-1",
             av[201]["ecart"] == -24 and av[201]["comparaison"] == "N-1",
             f"{av[201]['ecart']} {av[201]['comparaison']}")

    print("Alertes rouges")
    alerts.detecter()
    al = {(r["secteur_code"], r["cycle"]): r for r in alerts.lister()}
    a201 = al.get((201, "11/2026"))
    verifier("alerte 201 en N-1, comparée à 10/2026",
             a201 is not None and a201["comparaison"] == "N-1"
             and a201["cycle_precedent"] == "10/2026", str(a201))
    a202 = al.get((202, "11/2026"))
    verifier("alerte 202 en N-2, comparée à 09/2026",
             a202 is not None and a202["comparaison"] == "N-2"
             and a202["cycle_precedent"] == "09/2026", str(a202))


def main():
    print(f"Base de test : {_TMP}\n")
    preparer()
    try:
        tests()
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        verifier("exécution", False, str(exc))
    db.fermer()
    print("\nRÉSULTAT :", "TOUT EST OK" if ok else "AU MOINS UN ÉCHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
