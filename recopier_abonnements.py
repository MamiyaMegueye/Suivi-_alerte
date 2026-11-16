r"""
Recopie raw_abonnement depuis la sauvegarde faite par reparer_base.py.

À lancer APPLICATION ARRÊTÉE :

    .\.venv\Scripts\python.exe recopier_abonnements.py

Seules les colonnes communes aux deux tables sont recopiées (l'ancienne
table avait en plus ABN_DATE, que l'application n'utilise pas).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

RACINE = Path(__file__).resolve().parent
sys.path.insert(0, str(RACINE))

from src import config, db  # noqa: E402


def main() -> int:
    sauvegardes = sorted((RACINE / "_sauvegarde_reparation").glob(
        "baisse_avant_reparation_*.duckdb"))
    if not sauvegardes:
        print("Aucune sauvegarde trouvée dans _sauvegarde_reparation.")
        return 1
    source = sauvegardes[-1]
    print(f"Sauvegarde : {source.name}")

    con = duckdb.connect(str(config.DB_PATH))
    db._creer_schema(con)
    con.execute(f"ATTACH '{source}' AS old (READ_ONLY)")

    def colonnes(base: str) -> list[str]:
        return [r[0] for r in con.execute(
            "SELECT column_name FROM duckdb_columns() "
            "WHERE database_name = ? AND table_name = 'raw_abonnement' "
            "ORDER BY column_index", [base]).fetchall()]

    cibles = colonnes(con.execute("SELECT current_database()").fetchone()[0])
    sources = set(colonnes("old"))
    communes = [c for c in cibles if c in sources]
    liste = ", ".join(communes)

    con.execute("BEGIN TRANSACTION")
    con.execute("DELETE FROM raw_abonnement")
    con.execute(
        f"INSERT INTO raw_abonnement ({liste}) SELECT {liste} "
        f"FROM old.raw_abonnement "
        f"QUALIFY ROW_NUMBER() OVER (PARTITION BY ABN_ID) = 1")
    con.execute("COMMIT")
    n = con.execute("SELECT COUNT(*) FROM raw_abonnement").fetchone()[0]
    con.execute("DETACH old")
    con.execute("CHECKPOINT")
    con.close()
    print(f"raw_abonnement : {n:,} lignes recopiées.")
    print("Tu peux relancer l'application.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
