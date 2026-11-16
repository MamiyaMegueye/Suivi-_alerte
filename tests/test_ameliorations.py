"""
Test des améliorations du 12/11/2026, sur une base DuckDB JETABLE.
Ne touche ni à data/baisse.duckdb ni à Oracle.

    .venv\\Scripts\\python.exe -m tests.test_ameliorations

Secteur 301 (bimestriel), cycles 05, 07, 09, 11/2026 :
  abonné 5001  29, 29, 225 (pic), puis 17   -> référence 29, pas 225
  abonné 5002  10, 10, 10, puis 10          -> stable
  abonné 5003  20 en 09, rien en 11         -> abonné perdu
Attendu au secteur : référence 39 (29 + 10), volume N 27, écart -12,
volume N-2 brut 235, 1 abonné perdu pesant 20 m³.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp()) / "test_amelio.duckdb"
os.environ["BAISSE_DB"] = str(_TMP)
os.environ["WATCHER_ACTIF"] = "0"

import pandas as pd  # noqa: E402

from src import config, db, extractor, oracle_client, queries  # noqa: E402

assert Path(config.DB_PATH) == _TMP, "garde-fou : mauvaise base"
ok = True


def verifier(nom, cond, detail=""):
    global ok
    print(("  OK    " if cond else "  ÉCHEC ") + nom
          + (f"  ({detail})" if detail and not cond else ""))
    ok &= bool(cond)


def preparer():
    db.initialiser()
    db.executer("INSERT INTO ref_centre VALUES (10, 'CENTRE TEST', 2)")
    db.executer("INSERT INTO ref_secteur VALUES (301, 'SECT 301', '301', 10, '1')")
    fid = [0]

    def f(abn, m, vol):
        fid[0] += 1
        db.executer(
            "INSERT INTO raw_facture_m (FACT_ID, FACT_NUM, STR_ID, SECT_ID, ABN_ID, "
            "FACT_ANNEE, FACT_PERIODE, TYPE_FACT, FAT_QTE_CONS, FLAGANNULLE) "
            "VALUES (?, ?, 10, 301, ?, 2026, ?, 0, ?, 0)", [fid[0], fid[0], abn, m, vol])

    for m, v in ((5, 29), (7, 29), (9, 225), (11, 17)):
        f(5001, m, v)
    for m in (5, 7, 9, 11):
        f(5002, m, 10)
    f(5003, 9, 20)
    for i, m in enumerate((9, 11), start=1):
        db.executer(
            "INSERT INTO raw_cloture_secteur (IDCLOTURE, STR_ID, SECT_ID, ANNEE, MOIS, "
            "NBABO, NBTRAITER, POURCTRAITER, VOLUMEFACT, VOLUMESTIM, CLOTURE) "
            "VALUES (?, 10, 301, 2026, ?, 3, 3, 100, 0, 0, 1)", [i, m])


def tests():
    print("Référence = médiane des 3 dernières factures relevées")
    s = {r["secteur_code"]: r for r in queries.secteurs(2026, 11, seulement_baisses=False)}
    r = s.get(301, {})
    verifier("secteur : référence 39, volume N 27, écart -12",
             (r.get("vol_p"), r.get("vol_n"), r.get("ecart")) == (39, 27, -12),
             f"{r.get('vol_p')} / {r.get('vol_n')} / {r.get('ecart')}")
    verifier("secteur : volume N-2 brut 235", r.get("vol_p_cycle") == 235,
             str(r.get("vol_p_cycle")))

    print("Abonnés perdus")
    verifier("secteur : 1 abonné perdu, 20 m³",
             (r.get("nb_perdus"), r.get("vol_perdus")) == (1, 20),
             f"{r.get('nb_perdus')} / {r.get('vol_perdus')}")
    syn = queries.synthese(2026, 11)
    verifier("synthèse : 1 perdu, 20 m³",
             (syn.get("nb_perdus"), syn.get("vol_perdus")) == (1, 20),
             f"{syn.get('nb_perdus')} / {syn.get('vol_perdus')}")

    print("Détail par abonné : part de la baisse")
    d = queries.abonnes(2026, 11, 10, 301)
    L = {x["reference"]: x for x in d["lignes"]}
    a = L.get(5001, {})
    verifier("5001 : N-2 brut 225, référence 29, N 17, écart -12",
             (a.get("vol_p_cycle"), a.get("vol_p"), a.get("vol_n"), a.get("ecart"))
             == (225, 29, 17, -12), str(a))
    verifier("5001 porte 100 % de la baisse", a.get("part_baisse") == 100.0,
             str(a.get("part_baisse")))
    verifier("5002 stable, sans part", L.get(5002, {}).get("part_baisse") is None)
    verifier("5003 (perdu, sans facture N) absent du détail", 5003 not in L)
    verifier("1 abonné = 80 % de la baisse", d["resume"]["nb_80pct"] == 1,
             str(d["resume"]["nb_80pct"]))
    verifier("résumé : 1 perdu, 20 m³",
             (d["resume"]["nb_perdus"], d["resume"]["vol_perdus"]) == (1, 20.0),
             str((d["resume"]["nb_perdus"], d["resume"]["vol_perdus"])))

    print("Journal : seulement les secteurs facturés et en baisse")
    from src import alerts
    db.executer(
        "INSERT INTO alertes_baisse (alert_id, cycle_key, ANNEE, MOIS, STR_ID, "
        "SECT_ID, vol_n, vol_p, ecart, ecart_pct, gravite, etat, fiabilite, "
        "detected_at, updated_at, acknowledged) VALUES (900, 202611, 2026, 11, "
        "10, 999, 0, 500, -500, -100, 'DISPARU', 'ABSENT', 'provisoire', "
        "now(), now(), FALSE)")
    alerts.detecter()
    journal = {x["secteur_code"] for x in alerts.lister(cycle=202611)}
    verifier("ancienne alerte « absent du cycle » retirée du journal",
             999 not in journal, str(journal))
    verifier("le secteur 301 (facturé, en baisse) y est", 301 in journal,
             str(journal))
    verifier("le bandeau ne compte plus l'ancienne alerte",
             alerts.compter(True)["n"] == 1, str(alerts.compter(True)))

    print("Factures du cycle en cours (requête Oracle, sans Oracle)")
    capt = {}

    def faux(sql, params=None):
        capt["sql"], capt["params"] = sql, params
        oracle_client._verifier_lecture_seule(sql)   # lève si écriture
        return pd.DataFrame()

    vrai = oracle_client.requete
    oracle_client.requete = faux
    try:
        extractor.facture_m_cycles([(2026, 11), (2026, 10)])
    finally:
        oracle_client.requete = vrai
    verifier("requête lecture seule sur les 2 cycles",
             capt.get("params") == {"a0": 2026, "m0": 11, "a1": 2026, "m1": 10}
             and capt["sql"].count("fm.FACT_PERIODE = :m") == 2,
             str(capt.get("params")))


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
