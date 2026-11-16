"""
Volumes issus de la relève : relevée (0), estimée (1) et forfaitaire (2)
comptent ; une mutation (4) ne compte pas. Base DuckDB JETABLE.

    .venv\\Scripts\\python.exe -m tests.test_types_facture

Secteur 401, cycles 09 et 11/2026 :
  6001  relevée 10 en 09, ESTIMÉE 6 en 11        -> comptée : -4
  6002  relevée 20 en 09, FORFAITAIRE 20 en 11   -> comptée : stable
  6003  relevée 30 en 09, MUTATION 5 en 11       -> pas comparée (perdue)
Attendu au secteur : 26 contre 30, écart -4, 1 abonné perdu.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp()) / "test_types.duckdb"
os.environ["BAISSE_DB"] = str(_TMP)
os.environ["WATCHER_ACTIF"] = "0"

from src import config, db, queries  # noqa: E402

assert Path(config.DB_PATH) == _TMP
ok = True


def verifier(nom, cond, detail=""):
    global ok
    print(("  OK    " if cond else "  ÉCHEC ") + nom
          + (f"  ({detail})" if detail and not cond else ""))
    ok &= bool(cond)


def main():
    print(f"Base de test : {_TMP}\n")
    db.initialiser()
    db.executer("INSERT INTO ref_centre VALUES (10, 'CENTRE TEST', 2)")
    db.executer("INSERT INTO ref_secteur VALUES (401, 'SECT 401', '401', 10, '1')")
    fid = [0]

    def f(abn, m, typ, vol):
        fid[0] += 1
        db.executer(
            "INSERT INTO raw_facture_m (FACT_ID, FACT_NUM, STR_ID, SECT_ID, ABN_ID, "
            "FACT_ANNEE, FACT_PERIODE, TYPE_FACT, FAT_QTE_CONS, FLAGANNULLE) "
            "VALUES (?, ?, 10, 401, ?, 2026, ?, ?, ?, 0)",
            [fid[0], fid[0], abn, m, typ, vol])

    f(6001, 9, 0, 10); f(6001, 11, 1, 6)
    f(6002, 9, 0, 20); f(6002, 11, 2, 20)
    f(6003, 9, 0, 30); f(6003, 11, 4, 5)
    for i, m in enumerate((9, 11), start=1):
        db.executer(
            "INSERT INTO raw_cloture_secteur (IDCLOTURE, STR_ID, SECT_ID, ANNEE, MOIS, "
            "NBABO, NBTRAITER, POURCTRAITER, VOLUMEFACT, VOLUMESTIM, CLOTURE) "
            "VALUES (?, 10, 401, 2026, ?, 3, 3, 100, 0, 0, 1)", [i, m])

    print("Secteur")
    r = {x["secteur_code"]: x for x in queries.secteurs(2026, 11, seulement_baisses=False)}.get(401, {})
    verifier("volume N 26 contre 30, écart -4",
             (r.get("vol_n"), r.get("vol_p"), r.get("ecart")) == (26, 30, -4),
             f"{r.get('vol_n')} / {r.get('vol_p')} / {r.get('ecart')}")
    verifier("la mutation n'est pas comparée : 1 abonné perdu", r.get("nb_perdus") == 1,
             str(r.get("nb_perdus")))

    print("Détail par abonné")
    L = {x["reference"]: x for x in queries.abonnes(2026, 11, 10, 401)["lignes"]}
    verifier("l'abonné estimé est affiché, écart -4",
             L.get(6001, {}).get("ecart") == -4 and L[6001]["type_facture"] == "Estimée",
             str(L.get(6001)))
    verifier("l'abonné au forfait est affiché, stable",
             L.get(6002, {}).get("ecart") == 0, str(L.get(6002)))

    db.fermer()
    print("\nRÉSULTAT :", "TOUT EST OK" if ok else "AU MOINS UN ÉCHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
