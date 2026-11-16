"""
Test du bandeau jaune (deadline proche puis retard), base DuckDB JETABLE.

    .venv\\Scripts\\python.exe -m tests.test_retards
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

_TMP = Path(tempfile.mkdtemp()) / "test_retards.duckdb"
os.environ["BAISSE_DB"] = str(_TMP)
os.environ["WATCHER_ACTIF"] = "0"

from src import alerts, config, db  # noqa: E402

assert Path(config.DB_PATH) == _TMP
ok = True


def verifier(nom, cond, detail=""):
    global ok
    print(("  OK    " if cond else "  ÉCHEC ") + nom
          + (f"  ({detail})" if detail and not cond else ""))
    ok &= bool(cond)


def deadline(j: date):
    db.executer("DELETE FROM raw_planif_detaille")
    db.executer("INSERT INTO raw_planif_detaille VALUES (1, 1, 7, 0, ?, ?, ?, ?)",
                [datetime.combine(j, datetime.min.time()),
                 datetime.combine(j, datetime.min.time()), AUJ.month, AUJ.year])


AUJ = date.today()


def main():
    print(f"Base de test : {_TMP}\n")
    db.initialiser()
    db.executer("INSERT INTO ref_centre VALUES (10, 'CENTRE TEST', 2)")
    db.executer("INSERT INTO ref_secteur VALUES (101, 'SECT A', '1', 10, '7804')")
    # Secteur non clôturé, saisie à 40 %, cycle du mois courant
    db.executer(
        "INSERT INTO raw_cloture_secteur (IDCLOTURE, STR_ID, SECT_ID, ANNEE, MOIS, "
        "NBABO, NBTRAITER, NBNONTRAITER, POURCTRAITER, VOLUMEFACT, VOLUMESTIM, CLOTURE) "
        "VALUES (1, 10, 101, ?, ?, 100, 40, 60, 40, 0, 0, 0)", [AUJ.year, AUJ.month])
    db.executer("INSERT INTO raw_planif_releve VALUES (7, 101, 10, 1, ?)", [AUJ.year])

    print("Veille du jour J")
    deadline(AUJ + timedelta(days=1))
    n = alerts.detecter_retards()
    c = alerts.compter_retards(True)
    verifier("une alerte « deadline proche » levée", n == 1 and c["nb_proches"] == 1,
             f"nouvelles={n} {c}")
    alerts.acquitter_tout_retard()
    verifier("consultée : bandeau éteint", alerts.compter_retards(True)["n"] == 0)

    print("Jour J : le secteur passe en retard")
    deadline(AUJ)
    n = alerts.detecter_retards()
    c = alerts.compter_retards(True)
    verifier("le bandeau revient (nouvelle alerte)", n == 1 and c["n"] == 1,
             f"nouvelles={n} {c}")
    verifier("compté en retard, plus en proche",
             c["nb_en_retard"] == 1 and c["nb_proches"] == 0, str(c))

    print("Le lendemain : pas de doublon")
    n = alerts.detecter_retards()
    verifier("aucune nouvelle alerte", n == 0, f"nouvelles={n}")

    db.fermer()
    print("\nRÉSULTAT :", "TOUT EST OK" if ok else "AU MOINS UN ÉCHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
