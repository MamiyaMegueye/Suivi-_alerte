r"""
Réparation de la base locale DuckDB.

À lancer APPLICATION ARRÊTÉE, depuis le dossier de l'application :

    .\.venv\Scripts\python.exe reparer_base.py

Reconstruit data\baisse.duckdb dans un fichier neuf : toutes les tables
sont recopiées (journal des alertes compris), sans les index qui faisaient
planter DuckDB. L'ancien fichier est déplacé, pas supprimé, dans
_sauvegarde_reparation\. Une table illisible est signalée et laissée
vide : si c'est une table brute, la veille la recharge depuis Oracle.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import duckdb

RACINE = Path(__file__).resolve().parent
sys.path.insert(0, str(RACINE))

from src import config, db  # noqa: E402

ancien = Path(config.DB_PATH)
neuf = ancien.with_name(ancien.stem + "_neuf.duckdb")
wal = Path(str(ancien) + ".wal")
sauvegarde = RACINE / "_sauvegarde_reparation"


def main() -> int:
    if not ancien.exists():
        print(f"Base introuvable : {ancien}")
        return 1
    print(f"DuckDB {duckdb.__version__}")
    print(f"Base à réparer : {ancien}\n")

    for f in (neuf, Path(str(neuf) + ".wal")):
        if f.exists():
            f.unlink()

    con = duckdb.connect(str(neuf))
    db._creer_schema(con)                     # schéma actuel, sans index
    try:
        con.execute(f"ATTACH '{ancien}' AS old (READ_ONLY)")
    except Exception as exc:  # noqa: BLE001
        print("Impossible d'ouvrir l'ancienne base :")
        print(f"  {str(exc).splitlines()[0][:300]}")
        print("\nSi l'application tourne encore, arrête-la (Ctrl+C) et "
              "relance ce script.")
        print(f"Sinon, déplace {wal.name} dans _sauvegarde_reparation et "
              "relance ce script.")
        con.close()
        return 1

    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM duckdb_tables() "
        "WHERE database_name = 'old' ORDER BY table_name"
    ).fetchall()]
    nouvelles = {r[0] for r in con.execute(
        "SELECT table_name FROM duckdb_tables() "
        "WHERE database_name = current_database()"
    ).fetchall()}

    echecs = []
    for t in tables:
        pk = db.TABLES_BRUTES.get(t)
        dedoublon = (f" QUALIFY ROW_NUMBER() OVER (PARTITION BY {pk}) = 1"
                     if pk else "")
        try:
            con.execute("BEGIN TRANSACTION")
            if t in nouvelles:
                # Colonnes communes seulement : une ancienne table peut en
                # avoir en plus (ex. ABN_DATE dans raw_abonnement).
                cols = lambda base: [r[0] for r in con.execute(  # noqa: E731
                    "SELECT column_name FROM duckdb_columns() "
                    "WHERE database_name = ? AND table_name = ? "
                    "ORDER BY column_index", [base, t]).fetchall()]
                src = set(cols("old"))
                communes = ", ".join(c for c in cols(
                    con.execute("SELECT current_database()").fetchone()[0])
                    if c in src)
                con.execute(f"INSERT INTO {t} ({communes}) "
                            f"SELECT {communes} FROM old.{t}{dedoublon}")
            else:
                con.execute(f"CREATE TABLE {t} AS "
                            f"SELECT * FROM old.{t}{dedoublon}")
            con.execute("COMMIT")
            n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:24} {n:>10,} lignes recopiées")
        except Exception as exc:  # noqa: BLE001
            try:
                con.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            echecs.append(t)
            print(f"  {t:24} ILLISIBLE — "
                  f"{str(exc).splitlines()[0][:120]}")

    con.execute("DETACH old")
    con.execute("CHECKPOINT")
    con.close()

    # Remplacement : l'ancien fichier part dans la sauvegarde.
    sauvegarde.mkdir(exist_ok=True)
    tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.replace(ancien, sauvegarde / f"baisse_avant_reparation_{tag}.duckdb")
    if wal.exists():
        os.replace(wal, sauvegarde / f"baisse_avant_reparation_{tag}.duckdb.wal")
    os.replace(neuf, ancien)

    print(f"\nBase réparée. Ancien fichier : {sauvegarde.name}\\")
    if echecs:
        print("Tables illisibles laissées vides : " + ", ".join(echecs))
        print("Les tables brutes seront rechargées depuis Oracle par la "
              "veille au démarrage.")
        if {"raw_abonnement", "raw_compteur"} & set(echecs):
            print("raw_abonnement / raw_compteur : lance aussi "
                  "« python -m src.etl » une fois, application arrêtée.")
    print("\nTu peux relancer l'application.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
