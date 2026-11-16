"""
Tests des corrections du 12/11/2026, sur une base DuckDB JETABLE.

Ne touche ni à data/baisse.duckdb ni à Oracle. Lancer depuis le dossier
de l'application :

    .venv\\Scripts\\python.exe -m tests.test_corrections
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Base temporaire AVANT tout import de src (config lit BAISSE_DB).
_TMP = Path(tempfile.mkdtemp()) / "test.duckdb"
os.environ["BAISSE_DB"] = str(_TMP)
os.environ["WATCHER_ACTIF"] = "0"

import pandas as pd  # noqa: E402

from src import alerts, config, db, queries  # noqa: E402

assert Path(config.DB_PATH) == _TMP, "garde-fou : la base de test n'est pas la bonne"

ok = True


def verifier(nom: str, cond: bool, detail: str = "") -> None:
    global ok
    print(("  OK    " if cond else "  ÉCHEC ") + nom + (f"  ({detail})" if detail and not cond else ""))
    ok &= bool(cond)


def preparer() -> None:
    db.initialiser()
    db.executer("INSERT INTO ref_centre VALUES (10, 'CENTRE TEST', 2)")
    db.executer("INSERT INTO ref_secteur VALUES (101, 'SECT A', '1', 10, '7804.0')")
    db.executer("INSERT INTO ref_secteur VALUES (102, 'SECT B', '2', 10, '7805')")
    # Cycle 11/2026 : secteur 101 NON clôturé avec 1 facture isolée,
    #                 secteur 102 clôturé avec 1 facture.
    for idc, sect, clot in ((1, 101, 0), (2, 102, 1)):
        db.executer(
            "INSERT INTO raw_cloture_secteur (IDCLOTURE, STR_ID, SECT_ID, ANNEE, MOIS, "
            "NBABO, NBTRAITER, NBNONTRAITER, POURCTRAITER, VOLUMEFACT, VOLUMESTIM, CLOTURE) "
            "VALUES (?, 10, ?, 2026, 11, 100, 40, 60, 40, 500, 0, ?)",
            [idc, sect, clot])
    for fid, sect in ((1, 101), (2, 102)):
        db.executer(
            "INSERT INTO raw_facture_m (FACT_ID, STR_ID, SECT_ID, ABN_ID, FACT_ANNEE, "
            "FACT_PERIODE, TYPE_FACT, FAT_QTE_CONS, FLAGANNULLE) "
            "VALUES (?, 10, ?, ?, 2026, 11, 0, 10, 0)", [fid, sect, 1000 + fid])
    # Cycle N-2 (09/2026) du secteur 102 : même abonné 1002, 15 m³ relevés.
    # VOLUMEFACT vaut 500 partout : s'il servait encore, l'écart serait 0.
    db.executer(
        "INSERT INTO raw_cloture_secteur (IDCLOTURE, STR_ID, SECT_ID, ANNEE, MOIS, "
        "NBABO, NBTRAITER, POURCTRAITER, VOLUMEFACT, VOLUMESTIM, CLOTURE) "
        "VALUES (3, 10, 102, 2026, 9, 100, 100, 100, 500, 0, 1)")
    db.executer(
        "INSERT INTO raw_facture_m (FACT_ID, STR_ID, SECT_ID, ABN_ID, FACT_ANNEE, "
        "FACT_PERIODE, TYPE_FACT, FAT_QTE_CONS, FLAGANNULLE) "
        "VALUES (3, 10, 102, 1002, 2026, 9, 0, 15, 0)")
    db.executer(
        "INSERT INTO alertes_retard (alert_id, cycle_key, ANNEE, MOIS, STR_ID, SECT_ID, "
        "jours_retard, detected_at, updated_at, acknowledged) "
        "VALUES (1, 202611, 2026, 11, 10, 101, 3, now(), now(), FALSE)")


def test_avancement_facture() -> None:
    print("Point 2 — Avancement : « Facturé » exige la clôture")
    rows = {r["secteur_code"]: r for r in queries.avancement(2026, 11)}
    verifier("secteur non clôturé + 1 facture n'est PAS FACTURE",
             rows[101]["etat"] != "FACTURE", rows[101]["etat"])
    verifier("secteur clôturé + factures est FACTURE",
             rows[102]["etat"] == "FACTURE", rows[102]["etat"])
    ret = {r["secteur_code"]: r for r in queries.retards(2026, 11)}
    verifier("même état que l'onglet Retards",
             all(rows[k]["etat"] == ret[k]["etat"] for k in (101, 102)),
             str({k: (rows[k]["etat"], ret[k]["etat"]) for k in (101, 102)}))


def test_avancement_ecart() -> None:
    print("Point 1 — Avancement : même écart que l'onglet Baisses")
    av = {r["secteur_code"]: r for r in queries.avancement(2026, 11)}
    se = {r["secteur_code"]: r for r in queries.secteurs(2026, 11, seulement_baisses=False)}
    verifier("secteur 102 : écart -5 m³ (10 contre 15), pas VOLUMEFACT",
             av[102]["ecart"] == -5 and se[102]["ecart"] == -5,
             f"avancement {av[102]['ecart']}, baisses {se[102]['ecart']}")
    verifier("écart % identique", av[102]["ecart_pct"] == se[102]["ecart_pct"],
             f"{av[102]['ecart_pct']} / {se[102]['ecart_pct']}")


def test_matricule_retard() -> None:
    print("Point 6 — Journal des retards : matricule nettoyé")
    r = alerts.lister_retards()[0]
    verifier("« 7804.0 » devient « 7804 »",
             r["matricule_releveur"] == "7804", repr(r["matricule_releveur"]))


def test_remplacer_transaction() -> None:
    print("Point 7 — remplacer() : tout ou rien")
    avant = db.valeur("SELECT COUNT(*) FROM ref_centre")
    # Clé en double : l'INSERT échoue -> le DELETE doit être annulé.
    mauvais = pd.DataFrame({"CENTRE_ID": [20, 20], "CENTRE_LIB": ["X", "Y"],
                            "ZONE_ID": [2, 2]})
    try:
        db.remplacer(mauvais, "ref_centre")
        verifier("l'insertion en double doit échouer", False)
    except Exception:  # noqa: BLE001
        pass
    apres = db.valeur("SELECT COUNT(*) FROM ref_centre")
    verifier("ref_centre n'est pas vidée après un échec",
             apres == avant, f"avant {avant}, après {apres}")
    bon = pd.DataFrame({"CENTRE_ID": [30], "CENTRE_LIB": ["NEUF"], "ZONE_ID": [3]})
    db.remplacer(bon, "ref_centre")
    verifier("un rechargement valide remplace bien",
             db.valeur("SELECT string_agg(CENTRE_LIB, ',') FROM ref_centre") == "NEUF")


def main() -> int:
    print(f"Base de test : {_TMP}\n")
    preparer()
    for t in (test_avancement_facture, test_avancement_ecart, test_matricule_retard, test_remplacer_transaction):
        try:
            t()
        except Exception as exc:  # noqa: BLE001
            verifier(t.__name__, False, f"exception : {exc}")
    db.fermer()
    print("\nRÉSULTAT :", "TOUT EST OK" if ok else "AU MOINS UN ÉCHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
