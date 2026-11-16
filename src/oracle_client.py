"""
Client Oracle — lecture seule, avec garde-fous.

Reprend la couche de sécurité du dashboard : seules les requêtes
SELECT / WITH passent, l'autocommit est coupé, un rollback est forcé à la
fermeture, et un timeout est posé côté driver. Aucune écriture ne peut
partir vers la base de production, même par erreur de code.

Les identifiants viennent du `.env` de cette application et ne sont jamais
écrits dans les logs.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Iterator

import pandas as pd

from src import config

try:
    import oracledb
except ImportError:  # noqa: BLE001
    oracledb = None  # type: ignore[assignment]


# ============================================================
# Validation des requêtes
# ============================================================

_SAFE_QUERY_RE = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)

_DANGEROUS_KEYWORDS_RE = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|MERGE|"
    r"DROP|TRUNCATE|ALTER|CREATE|RENAME|"
    r"GRANT|REVOKE|"
    r"EXEC|EXECUTE|CALL|"
    r"COMMIT|ROLLBACK|SAVEPOINT"
    r")\b",
    re.IGNORECASE,
)


class ReadOnlyViolation(RuntimeError):
    """Une requête tente de modifier la base de production."""


class OracleIndisponible(RuntimeError):
    """Driver absent, identifiants manquants, ou serveur injoignable."""


def _sans_commentaires(sql: str) -> str:
    """Retire les commentaires avant validation, pour qu'un INSERT ne
    puisse pas se cacher dans /* ... */ ou derrière un --."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def _verifier_lecture_seule(sql: str) -> None:
    if not sql or not sql.strip():
        raise ReadOnlyViolation("Requête vide refusée.")

    propre = _sans_commentaires(sql).strip()

    if not _SAFE_QUERY_RE.match(propre):
        raise ReadOnlyViolation(
            "Seules les requêtes SELECT / WITH sont autorisées vers "
            f"Oracle. Reçu : {propre[:120]}…"
        )

    trouve = _DANGEROUS_KEYWORDS_RE.search(propre)
    if trouve:
        raise ReadOnlyViolation(
            f"Mot-clé d'écriture détecté : '{trouve.group(1).upper()}'. "
            "Requête refusée."
        )


# ============================================================
# Connexion
# ============================================================

@contextmanager
def connexion() -> Iterator["oracledb.Connection"]:
    if oracledb is None:
        raise OracleIndisponible(
            "Le module « oracledb » n'est pas installé.\n"
            "Lance : python -m pip install -r requirements.txt"
        )
    if not config.oracle_configure():
        raise OracleIndisponible(
            "Identifiants Oracle absents.\n\n"
            f"Crée le fichier {config.ENV_FILE} en copiant .env.example, "
            "puis renseigne ORACLE_USER et ORACLE_PASSWORD.\n"
            "Tu peux aussi recopier le .env du dashboard "
            "(voir README, section 2)."
        )

    conn = oracledb.connect(
        user=config.ORACLE_USER,
        password=config.ORACLE_PASSWORD,
        dsn=config.ORACLE_DSN,
    )
    # Aucune écriture ne sera commitée, même par accident.
    conn.autocommit = False
    try:
        conn.call_timeout = config.ORACLE_QUERY_TIMEOUT_SEC * 1000
    except AttributeError:
        pass

    try:
        yield conn
    finally:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def requete(sql: str, params: dict | None = None) -> pd.DataFrame:
    """Exécute un SELECT et renvoie un DataFrame aux colonnes majuscules."""
    _verifier_lecture_seule(sql)
    with connexion() as conn:
        with conn.cursor() as cur:
            cur.arraysize = config.ORACLE_FETCH_BATCH
            cur.prefetchrows = config.ORACLE_FETCH_BATCH + 1
            cur.execute(sql, params or {})
            colonnes = [d[0].upper() for d in cur.description]
            lignes = cur.fetchall()
    return pd.DataFrame(lignes, columns=colonnes)


def tester() -> dict:
    """Diagnostic de connexion, pour /api/etat et le script d'install."""
    if oracledb is None:
        return {"ok": False, "erreur": "module oracledb absent"}
    if not config.oracle_configure():
        return {"ok": False, "erreur": "identifiants absents du .env"}
    try:
        df = requete("SELECT 1 AS OK FROM DUAL")
        return {"ok": not df.empty, "dsn": config.ORACLE_DSN}
    except Exception as exc:  # noqa: BLE001
        # On ne renvoie jamais la requête ni les identifiants.
        return {"ok": False, "erreur": str(exc).splitlines()[0][:200]}
