"""Accès Oracle en lecture seule (python-oracledb, mode thin, pool de connexions).

Ce module n'exécute que des SELECT. Un compte Oracle en lecture seule suffit.
"""
import logging
import threading

import oracledb

from .config import get_settings

log = logging.getLogger("controle.db")

# Durée maximale d'une requête (ms), pour ne jamais bloquer la base de production.
DELAI_REQUETE_MS = 180_000
# Attente maximale d'une connexion libre dans le pool (ms).
ATTENTE_CONNEXION_MS = 30_000

_pool = None
_verrou = threading.Lock()


class BaseIndisponible(RuntimeError):
    """La base Oracle n'est pas joignable ou le .env est incomplet."""


def get_pool():
    global _pool
    if _pool is not None:
        return _pool
    with _verrou:
        if _pool is None:
            s = get_settings()
            if not s.oracle_configure:
                raise BaseIndisponible(
                    "Connexion Oracle non configurée : renseignez ORACLE_USER, "
                    "ORACLE_PASSWORD, ORACLE_HOST et ORACLE_SERVICE_NAME dans backend/.env"
                )
            try:
                _pool = oracledb.create_pool(
                    user=s.oracle_user,
                    password=s.oracle_password,
                    dsn=s.dsn,
                    min=s.oracle_pool_min,
                    max=max(s.oracle_pool_max, s.oracle_pool_min, 1),
                    increment=max(s.oracle_pool_increment, 1),
                    # Ne jamais attendre indéfiniment une connexion libre.
                    getmode=oracledb.POOL_GETMODE_TIMEDWAIT,
                    wait_timeout=ATTENTE_CONNEXION_MS,
                )
                log.info("Pool Oracle créé sur %s", s.dsn)
            except oracledb.Error as exc:
                raise BaseIndisponible(f"Connexion Oracle impossible : {exc}") from exc
    return _pool


def fermer_pool():
    global _pool
    with _verrou:
        if _pool is not None:
            try:
                _pool.close(force=True)
            except oracledb.Error:
                pass
            _pool = None


def fetch_all(sql: str, params: dict | None = None, delai_ms: int | None = None) -> list[dict]:
    """Exécute un SELECT et retourne les lignes sous forme de dictionnaires
    (noms de colonnes en minuscules)."""
    pool = get_pool()
    try:
        with pool.acquire() as conn:
            conn.call_timeout = delai_ms or DELAI_REQUETE_MS
            with conn.cursor() as cur:
                cur.arraysize = 2000
                cur.prefetchrows = 2001
                cur.execute(sql, params or {})
                colonnes = [c[0].lower() for c in cur.description]
                return [dict(zip(colonnes, ligne)) for ligne in cur]
    except oracledb.Error as exc:
        raise BaseIndisponible(f"Erreur Oracle : {exc}") from exc


def ping() -> bool:
    fetch_all("SELECT 1 AS ok FROM dual")
    return True
