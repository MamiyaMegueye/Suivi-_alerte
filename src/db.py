"""
Base locale DuckDB — propriété exclusive de cette application.

Le fichier `data/baisse.duckdb` n'appartient qu'à cette application : rien
d'autre ne l'ouvre, donc aucun conflit de verrou possible. La connexion est
unique (singleton) et partagée par curseurs, comme le recommande DuckDB
pour le multi-thread.
"""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Any, Iterator

import duckdb
import pandas as pd

from src import config


class BaseIndisponible(RuntimeError):
    """Base absente, vide, ou impossible à ouvrir — message pour l'écran."""


_con: duckdb.DuckDBPyConnection | None = None
_lock = Lock()


# ============================================================
# Schéma
# ============================================================

DDL = """
CREATE TABLE IF NOT EXISTS raw_cloture_secteur (
    IDCLOTURE BIGINT,
    STR_ID BIGINT, SECT_ID BIGINT, ANNEE BIGINT, MOIS BIGINT,
    NBABO BIGINT, NBTRAITER BIGINT, NBNONTRAITER BIGINT,
    POURCTRAITER BIGINT, POURCNONTRAITER BIGINT,
    VOLUMEFACT DOUBLE, VOLUMESTIM DOUBLE,
    CLOTURE BIGINT, FLAG_CALCUL BIGINT, FLAG_VALID BIGINT,
    MATRICULE VARCHAR, DATE_CLOT TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw_facture_m (
    FACT_ID BIGINT,
    FACT_NUM BIGINT, STR_ID BIGINT, SECT_ID BIGINT,
    ABN_ID BIGINT, CLT_ID BIGINT, REL_ID BIGINT, TOUR_ID BIGINT,
    FACT_DATE TIMESTAMP, FACT_PERIODE BIGINT, FACT_ANNEE BIGINT,
    TYPE_FACT BIGINT, CODE_FACTURE BIGINT,
    FAT_QTE_CONS DOUBLE, FACT_MNT DOUBLE, FACT_MNT_TTC DOUBLE,
    NBESTIM BIGINT, FLAGANNULLE BIGINT, FLAG_REMB BIGINT
);

CREATE TABLE IF NOT EXISTS raw_releve (
    REL_ID BIGINT,
    ABN_ID BIGINT, CPT_REF VARCHAR, LOC_ID BIGINT, TOUR_ID BIGINT,
    STR_ID BIGINT, SECT_ID BIGINT, MATRICULE VARCHAR,
    REL_DATE TIMESTAMP, REL_ANNEE BIGINT, REL_MOIS BIGINT,
    DATE_GEN TIMESTAMP, REL_DATE_ANC_INDEX TIMESTAMP,
    REL_INDEX DOUBLE, REL_ANCIEN_INDEX DOUBLE,
    REL_CONSOM_CALCUL DOUBLE, REL_MOYENNE_CONSOM DOUBLE,
    REL_NBR_JR BIGINT, ID_COMP BIGINT, TYP_REL_ID BIGINT,
    REL_VALIDE BIGINT, REL_ESTIMATIF BIGINT, REL_FACTURABLE_FLAG BIGINT,
    REL_MAT VARCHAR, REL_MESSAGE VARCHAR, REL_ORIGINE VARCHAR
);

CREATE TABLE IF NOT EXISTS raw_abonnement (
    ABN_ID BIGINT,
    STR_ID BIGINT, SECT_ID BIGINT, CLI_ID BIGINT, ABN_RES BIGINT,
    ABN_ADRESSE VARCHAR, COMP_ADRESSE VARCHAR,
    QUARTIER VARCHAR, NLOT VARCHAR,
    ANCIENNE_REFRENCE VARCHAR,
    TAR_ID BIGINT, GRP_ID BIGINT, CAL_ID BIGINT
);

CREATE TABLE IF NOT EXISTS raw_compteur (
    ID BIGINT, ABN_ID BIGINT,
    CPT_SERIE VARCHAR, CPI_REF VARCHAR, CPI_ACTIF BIGINT,
    CPI_DATE_INS TIMESTAMP, CPI_DATE_DEPOSE TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw_planif_releve (
    PLAN_REL_ID BIGINT, SECT_ID BIGINT, STR_ID BIGINT,
    SEMAINEDEBT BIGINT, ANNEE BIGINT
);

CREATE TABLE IF NOT EXISTS raw_planif_detaille (
    IDDET BIGINT,
    NUMSEMAINE BIGINT, PLAN_REL_ID BIGINT, TYPE BIGINT,
    DATEDEBUTSEMAINE TIMESTAMP, DATEDEBUTFACT TIMESTAMP,
    MOIS BIGINT, ANNEE BIGINT
);

CREATE TABLE IF NOT EXISTS ref_centre (
    CENTRE_ID BIGINT PRIMARY KEY, CENTRE_LIB VARCHAR, ZONE_ID BIGINT
);

CREATE TABLE IF NOT EXISTS ref_secteur (
    SECT_ID BIGINT PRIMARY KEY, SECT_LIB VARCHAR, SECT_CODE VARCHAR,
    STR_ID BIGINT, MAT_RELEV VARCHAR
);

CREATE TABLE IF NOT EXISTS ref_comptage (
    ID_COMP BIGINT PRIMARY KEY, LIBELLE VARCHAR
);

-- Journal des alertes de baisse. Une ligne par (cycle, centre, secteur).
-- `acknowledged` ne repasse jamais à FALSE tout seul : une alerte
-- consultée reste consultée.
CREATE TABLE IF NOT EXISTS alertes_baisse (
    alert_id BIGINT PRIMARY KEY,
    cycle_key BIGINT,
    ANNEE BIGINT, MOIS BIGINT,
    STR_ID BIGINT, SECT_ID BIGINT,
    vol_n DOUBLE, vol_p DOUBLE,
    ecart DOUBLE, ecart_pct DOUBLE,
    gravite VARCHAR, etat VARCHAR, fiabilite VARCHAR,
    detected_at TIMESTAMP, updated_at TIMESTAMP,
    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_at TIMESTAMP
);

-- Journal des alertes de retard de facturation. Même forme que
-- alertes_baisse : une ligne par (cycle, centre, secteur), et
-- `acknowledged` ne repasse jamais à FALSE tout seul.
CREATE TABLE IF NOT EXISTS alertes_retard (
    alert_id BIGINT PRIMARY KEY,
    cycle_key BIGINT,
    ANNEE BIGINT, MOIS BIGINT,
    STR_ID BIGINT, SECT_ID BIGINT,
    deadline TIMESTAMP,
    jours_retard BIGINT,
    parc BIGINT, pct_saisie BIGINT,
    cloture BIGINT, nb_factures BIGINT,
    etat VARCHAR, urgence VARCHAR,
    detected_at TIMESTAMP, updated_at TIMESTAMP,
    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_at TIMESTAMP
);

-- Suivi des chargements : sert à afficher la fraîcheur des données.
CREATE TABLE IF NOT EXISTS etl_runs (
    run_id BIGINT PRIMARY KEY,
    etape VARCHAR, lignes BIGINT,
    debut TIMESTAMP, fin TIMESTAMP,
    statut VARCHAR, message VARCHAR
);
"""

INDEX = (
    "CREATE INDEX IF NOT EXISTS i_al_cycle ON alertes_baisse(cycle_key)",
    "CREATE INDEX IF NOT EXISTS i_ar_cycle ON alertes_retard(cycle_key)",
)

# Tables brutes rechargées depuis Oracle, avec leur clé.
#
# ⚠️ Elles n'ont plus ni clé primaire ni index. Le 12/11/2026, DuckDB a
# planté en rechargeant raw_releve (« Failed to delete all rows from
# index ») et a bloqué toute la base : les index ART supportent mal la
# suppression puis la réinsertion de centaines de milliers de lignes, et
# un chargement interrompu peut les désynchroniser. L'unicité de la clé
# est désormais garantie par upsert(), dans une transaction ; les
# lectures restent rapides sans index (balayage en colonnes).
TABLES_BRUTES = {
    "raw_cloture_secteur": "IDCLOTURE",
    "raw_facture_m": "FACT_ID",
    "raw_releve": "REL_ID",
    "raw_abonnement": "ABN_ID",
    "raw_compteur": "ID",
    "raw_planif_detaille": "IDDET",
}
_ANCIENS_INDEX = ("i_cs_cycle", "i_cs_sect", "i_fm_cycle", "i_fm_sect",
                  "i_rel_cycle", "i_cpt_abn", "i_pr_sect", "i_pd_plan")


# ============================================================
# Connexion
# ============================================================

def _ouvrir() -> duckdb.DuckDBPyConnection:
    global _con
    if _con is not None:
        return _con
    with _lock:
        if _con is not None:
            return _con
        chemin = config.DB_PATH
        chemin.parent.mkdir(parents=True, exist_ok=True)
        try:
            _con = duckdb.connect(str(chemin))
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "lock" in msg or "being used" in msg or "conflicting" in msg:
                raise BaseIndisponible(
                    f"La base locale {chemin} est déjà ouverte par un "
                    "autre processus.\n\n"
                    "Une seule instance à la fois : l'application et le "
                    "script de chargement ne peuvent pas tourner ensemble.\n\n"
                    "  • si l'application tourne, utilise le bouton "
                    "« Rafraîchir » de l'interface plutôt que "
                    "« python -m src.etl » ;\n"
                    "  • sinon, ferme l'autre fenêtre puis réessaie."
                ) from exc
            raise BaseIndisponible(f"Ouverture impossible : {exc}") from exc
        _creer_schema(_con)
        return _con


def _migrer_sans_index(con: duckdb.DuckDBPyConnection) -> None:
    """Base créée avant le 12/11/2026 : retire index et clés primaires des
    tables brutes. Chaque table est recopiée sans doublon de clé, puis
    remplace l'ancienne — ce qui supprime aussi un index désynchronisé."""
    for nom in _ANCIENS_INDEX:
        try:
            con.execute(f"DROP INDEX IF EXISTS {nom}")
        except Exception:  # noqa: BLE001
            pass
    for table, pk in TABLES_BRUTES.items():
        a_une_cle = con.execute(
            "SELECT COUNT(*) FROM duckdb_constraints() "
            "WHERE table_name = ? AND constraint_type = 'PRIMARY KEY'",
            [table],
        ).fetchone()[0]
        if not a_une_cle:
            continue
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(f"DROP TABLE IF EXISTS {table}__neuf")
            con.execute(
                f"CREATE TABLE {table}__neuf AS SELECT * FROM {table} "
                f"QUALIFY ROW_NUMBER() OVER (PARTITION BY {pk}) = 1"
            )
            con.execute(f"DROP TABLE {table}")
            con.execute(f"ALTER TABLE {table}__neuf RENAME TO {table}")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise


def _creer_schema(con: duckdb.DuckDBPyConnection) -> None:
    for bloc in DDL.split(";"):
        if bloc.strip():
            con.execute(bloc)
    _migrer_sans_index(con)
    # Gros consommateurs (12/11/2026) : « N-2 » ou « N-1 ». Les alertes
    # plus anciennes restent à NULL, c'est-à-dire N-2.
    con.execute("ALTER TABLE alertes_baisse "
                "ADD COLUMN IF NOT EXISTS comparaison VARCHAR")
    # Alerte toujours d'actualité (secteur facturé et en baisse au dernier
    # passage). Les autres restent en base mais sortent du journal.
    con.execute("ALTER TABLE alertes_baisse "
                "ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
    con.execute("ALTER TABLE alertes_baisse "
                "ADD COLUMN IF NOT EXISTS vol_total_n DOUBLE")
    con.execute("ALTER TABLE alertes_baisse "
                "ADD COLUMN IF NOT EXISTS vol_total_p DOUBLE")
    con.execute("ALTER TABLE alertes_baisse "
                "ADD COLUMN IF NOT EXISTS ecart_total DOUBLE")
    con.execute("ALTER TABLE alertes_baisse "
                "ADD COLUMN IF NOT EXISTS ecart_total_pct DOUBLE")
    for ddl in INDEX:
        try:
            con.execute(ddl)
        except Exception:  # noqa: BLE001
            pass


def initialiser() -> None:
    """Crée le fichier et le schéma s'ils n'existent pas."""
    _ouvrir()


def fermer() -> None:
    global _con
    with _lock:
        if _con is not None:
            try:
                _con.close()
            finally:
                _con = None


@contextmanager
def curseur() -> Iterator[duckdb.DuckDBPyConnection]:
    cur = _ouvrir().cursor()
    try:
        yield cur
    finally:
        try:
            cur.close()
        except Exception:  # noqa: BLE001
            pass


# ============================================================
# Lecture
# ============================================================

def lignes(sql: str, params: list | None = None) -> list[dict[str, Any]]:
    with curseur() as cur:
        cur.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def ligne(sql: str, params: list | None = None) -> dict[str, Any]:
    res = lignes(sql, params)
    return res[0] if res else {}


def valeur(sql: str, params: list | None = None) -> Any:
    with curseur() as cur:
        cur.execute(sql, params or [])
        r = cur.fetchone()
        return r[0] if r else None


def executer(sql: str, params: list | None = None) -> None:
    with curseur() as cur:
        cur.execute(sql, params or [])


# ============================================================
# Écriture — upsert depuis un DataFrame
# ============================================================

def upsert(df: pd.DataFrame, table: str, pk: str) -> int:
    """INSERT OR REPLACE d'un DataFrame, colonnes alignées sur la table.

    Les colonnes absentes du DataFrame sont créées à NULL, celles en trop
    sont ignorées : l'ordre du SELECT Oracle n'a donc pas d'importance.
    """
    if df is None or df.empty:
        return 0

    with curseur() as cur:
        cibles = [r[1] for r in cur.execute(
            f"PRAGMA table_info('{table}')"
        ).fetchall()]

        d = df.copy()
        d.columns = [c.upper() for c in d.columns]
        for c in cibles:
            if c.upper() not in d.columns:
                d[c.upper()] = None
        d = d[[c.upper() for c in cibles]]
        d = d.where(pd.notna(d), None)

        cur.register("_src", d)
        # Une seule transaction : en cas d'échec rien n'est à moitié
        # écrit, et les lectures ne voient jamais la table amputée.
        # Les doublons de clé venant d'Oracle sont écartés à l'insertion.
        cur.execute("BEGIN TRANSACTION")
        try:
            cur.execute(f"DELETE FROM {table} WHERE {pk} IN "
                        f"(SELECT {pk} FROM _src)")
            cur.execute(f"INSERT INTO {table} SELECT * FROM _src "
                        f"QUALIFY ROW_NUMBER() OVER (PARTITION BY {pk}) = 1")
            cur.execute("COMMIT")
        except Exception:
            try:
                cur.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            cur.unregister("_src")
    return len(df)


def remplacer(df: pd.DataFrame, table: str) -> int:
    """Vide la table puis la recharge — pour les petites références."""
    if df is None or df.empty:
        return 0
    with curseur() as cur:
        cibles = [r[1] for r in cur.execute(
            f"PRAGMA table_info('{table}')"
        ).fetchall()]
        d = df.copy()
        d.columns = [c.upper() for c in d.columns]
        for c in cibles:
            if c.upper() not in d.columns:
                d[c.upper()] = None
        d = d[[c.upper() for c in cibles]]
        d = d.where(pd.notna(d), None)
        cur.register("_src", d)
        # Une seule transaction : les lectures ne voient jamais la table
        # vide entre le DELETE et l'INSERT (libellés « (centre 12) »).
        cur.execute("BEGIN TRANSACTION")
        try:
            cur.execute(f"DELETE FROM {table}")
            cur.execute(f"INSERT INTO {table} SELECT * FROM _src")
            cur.execute("COMMIT")
        except Exception:
            try:
                cur.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            cur.unregister("_src")
    return len(df)


# ============================================================
# État
# ============================================================

def est_vide() -> bool:
    try:
        return (valeur("SELECT COUNT(*) FROM raw_cloture_secteur") or 0) == 0
    except Exception:  # noqa: BLE001
        return True


def info_source() -> dict[str, Any]:
    _ouvrir()
    runs = lignes("""
        SELECT etape, lignes, fin, statut
        FROM etl_runs
        WHERE statut = 'OK'
        QUALIFY ROW_NUMBER() OVER (PARTITION BY etape ORDER BY fin DESC) = 1
        ORDER BY etape
    """)
    return {
        "chemin": str(config.DB_PATH),
        "autonome": True,
        "vide": est_vide(),
        "chargements": runs,
        "derniere_maj": max(
            (r["fin"] for r in runs if r.get("fin")), default=None
        ),
    }


_lock_journal = Lock()


def journaliser(etape: str, lignes_n: int, debut, fin,
                statut: str = "OK", message: str = "") -> None:
    # Numéro et insertion sous verrou : deux écritures simultanées
    # prenaient le même run_id.
    with _lock_journal:
        nid = (valeur("SELECT COALESCE(MAX(run_id), 0) + 1 FROM etl_runs")
               or 1)
        executer(
            "INSERT INTO etl_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            [nid, etape, int(lignes_n), debut, fin, statut, message[:500]],
        )
