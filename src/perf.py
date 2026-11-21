"""
Accélération de l'API — module indépendant, branché par 2 lignes dans api.py.

1. Mesure   : durée de chaque appel /api écrite dans le log
              (« ⏱ GET /api/abonnes?... 2350 ms MISS ») + en-tête X-Temps-ms.
2. Cache    : chaque réponse JSON GET /api/... est gardée en mémoire.
              Elle est vidée :
                - à chaque passage du watcher (dès qu'il publie maj /
                  nouvelle_baisse / nouveau_retard / erreur sur le flux SSE),
                - à chaque action POST (acquitter une alerte, rafraîchir…),
                - au plus tard après CACHE_TTL_SEC (sécurité).
              → un écran déjà ouvert se réaffiche instantanément.
3. GZip     : compression des réponses > 1 Ko (grosses listes, exports).
4. Préchauffe : après chaque vidage par le watcher, les écrans consultés
              récemment (dont les listes d'abonnés) sont recalculés en
              arrière-plan → au clic, la réponse est déjà prête.

Acquitter une alerte ne vide QUE les réponses /api/alertes… (journaux et
compteurs) : les listes d'abonnés, secteurs, synthèses restent en cache.

Rien n'est écrit en base. Pour désactiver : retirer les 2 lignes dans api.py.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware

from src import sse

log = logging.getLogger("uvicorn.error")

CACHE_TTL_SEC = 15 * 60
CACHE_MAX_ENTREES = 400
CACHE_MAX_OCTETS = 300 * 1024 * 1024
LOG_SEUIL_MS = 100           # n'écrire dans le log que les appels lents (MISS)

# Routes jamais mises en cache
EXCLUS = (
    "/api/stream",        # flux temps réel
    "/api/etat",          # état du watcher (doit être frais)
    "/api/oracle",        # test de connexion
    "/api/mutation-gc",   # a son propre cache
    "/api/export",        # fichiers Excel
    "/api/refresh",
    "/api/perf",
)

PRECHAUFFE_FENETRE_SEC = 45 * 60   # écrans demandés dans les 45 dernières minutes
PRECHAUFFE_MAX = 25                # au plus 25 écrans recalculés par passage

_cache: dict[str, tuple[float, int, list, bytes]] = {}
_octets = 0
_version = 0
_recents: dict[str, float] = {}    # clé → dernière demande par l'utilisateur
_app = None
_tache_prechauffe: asyncio.Task | None = None
stats = {"hit": 0, "miss": 0, "vidages": 0, "prechauffes": 0}


_memo: dict = {}


def memo(cle, fn):
    """Mémoïse un calcul Python jusqu'au prochain vidage (nouvelles données).
    Sert aux calculs internes répétés (ex. abonnés de chaque secteur)."""
    v = _version
    hit = _memo.get(cle)
    if hit is not None and hit[0] == v:
        return hit[1]
    res = fn()
    if _version == v:
        if len(_memo) > 3000:
            _memo.clear()
        _memo[cle] = (v, res)
    return res


def vider(_raison: str = "") -> None:
    """Invalide tout le cache (nouvelle version des données)."""
    global _octets, _version
    _cache.clear()
    _memo.clear()
    _octets = 0
    _version += 1
    stats["vidages"] += 1


def vider_prefixe(prefixe: str) -> None:
    """Invalide seulement les réponses dont l'URL commence par `prefixe`."""
    global _octets
    for k in [k for k in _cache if k.startswith(prefixe)]:
        _octets -= len(_cache[k][3])
        del _cache[k]


async def _appel_interne(cle: str) -> None:
    """Rejoue un GET à travers toute l'app (donc à travers le cache)."""
    chemin, _, qs = cle.partition("?")
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": chemin,
        "raw_path": chemin.encode(), "query_string": qs.encode("latin-1"),
        "root_path": "", "headers": [(b"x-prechauffe", b"1")],
        "client": ("127.0.0.1", 0), "server": ("127.0.0.1", 0),
    }

    async def recevoir():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def envoyer(_msg):
        return None

    await _app(scope, recevoir, envoyer)


async def _prechauffer() -> None:
    await asyncio.sleep(2)  # laisser la page recevoir l'événement SSE
    v = _version
    maintenant = time.time()
    cles = sorted((k for k, t in _recents.items()
                   if maintenant - t < PRECHAUFFE_FENETRE_SEC),
                  key=lambda k: -_recents[k])[:PRECHAUFFE_MAX]
    for k in list(_recents):
        if maintenant - _recents[k] >= PRECHAUFFE_FENETRE_SEC:
            del _recents[k]
    for cle in cles:
        if _version != v:      # nouvelles données arrivées entre-temps : stop
            return
        if cle in _cache:      # déjà redemandé par la page
            continue
        try:
            await _appel_interne(cle)
            stats["prechauffes"] += 1
        except Exception:  # noqa: BLE001
            pass


def _lancer_prechauffe() -> None:
    global _tache_prechauffe
    if _app is None:
        return
    try:
        if _tache_prechauffe and not _tache_prechauffe.done():
            _tache_prechauffe.cancel()
        _tache_prechauffe = asyncio.get_running_loop().create_task(_prechauffer())
    except RuntimeError:
        pass


def _ranger(cle: str, statut: int, entetes: list, corps: bytes) -> None:
    global _octets
    if cle in _cache:
        _octets -= len(_cache[cle][3])
    _cache[cle] = (time.time(), statut, entetes, corps)
    _octets += len(corps)
    while _cache and (len(_cache) > CACHE_MAX_ENTREES or _octets > CACHE_MAX_OCTETS):
        vieille = min(_cache, key=lambda k: _cache[k][0])
        _octets -= len(_cache[vieille][3])
        del _cache[vieille]


class PerfMiddleware:
    """Middleware ASGI : chronométrage + cache des GET /api JSON."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        t0 = time.perf_counter()
        methode = scope["method"]
        chemin = scope["path"]
        qs = scope.get("query_string", b"").decode("latin-1")
        cle = chemin + ("?" + qs if qs else "")
        est_api = chemin.startswith("/api/")
        cachable = (methode == "GET" and est_api
                    and not chemin.startswith(EXCLUS))

        if cachable and (b"x-prechauffe", b"1") not in scope.get("headers", []):
            _recents[cle] = time.time()

        # ---- Réponse servie depuis le cache
        if cachable:
            hit = _cache.get(cle)
            if hit and time.time() - hit[0] < CACHE_TTL_SEC:
                stats["hit"] += 1
                ms = (time.perf_counter() - t0) * 1000
                entetes = list(hit[2]) + [
                    (b"x-cache", b"HIT"),
                    (b"x-temps-ms", f"{ms:.0f}".encode()),
                ]
                await send({"type": "http.response.start",
                            "status": hit[1], "headers": entetes})
                await send({"type": "http.response.body", "body": hit[3]})
                return

        # ---- Calcul normal (on capte la réponse pour la garder)
        version_debut = _version
        capt = {"statut": 0, "entetes": [], "morceaux": [], "json": False}

        async def envoyer(msg):
            if msg["type"] == "http.response.start":
                capt["statut"] = msg["status"]
                capt["entetes"] = [(k, v) for k, v in msg.get("headers", [])
                                   if k.lower() not in (b"content-length",)]
                ct = dict((k.lower(), v) for k, v in msg.get("headers", [])).get(b"content-type", b"")
                capt["json"] = ct.startswith(b"application/json")
                ms = (time.perf_counter() - t0) * 1000
                msg = dict(msg)
                msg["headers"] = list(msg.get("headers", [])) + [
                    (b"x-cache", b"MISS" if cachable else b"NO"),
                    (b"x-temps-ms", f"{ms:.0f}".encode()),
                ]
            elif msg["type"] == "http.response.body" and cachable and capt["json"]:
                capt["morceaux"].append(msg.get("body", b""))
            await send(msg)

        try:
            await self.app(scope, receive, envoyer)
        finally:
            if methode != "GET" and est_api:
                if chemin.startswith("/api/alertes"):
                    # acquittement : seuls journaux + compteurs d'alertes changent
                    vider_prefixe("/api/alertes")
                else:
                    vider("POST " + chemin)
                    _lancer_prechauffe()

        ms = (time.perf_counter() - t0) * 1000
        if cachable:
            stats["miss"] += 1
            if (capt["statut"] == 200 and capt["json"]
                    and _version == version_debut):  # pas de nouvelles données entre-temps
                corps = b"".join(capt["morceaux"])
                entetes = capt["entetes"] + [(b"content-length", str(len(corps)).encode())]
                _ranger(cle, 200, entetes, corps)
        if est_api and ms >= LOG_SEUIL_MS:
            log.info("⏱ %s %s %d ms %s", methode, cle, ms,
                     "MISS" if cachable else "")


SQL_MAP = """
    CREATE OR REPLACE TABLE abn_secteur_actuel AS
    SELECT ABN_ID AS sa_abn,
           ANY_VALUE(STR_ID)  AS STR_ID_ACT,
           ANY_VALUE(SECT_ID) AS SECT_ID_ACT
    FROM raw_abonnement
    WHERE STR_ID IS NOT NULL AND SECT_ID IS NOT NULL
    GROUP BY ABN_ID
"""


def rafraichir_secteur_actuel() -> None:
    """Table abonné → secteur ACTUEL, utilisée par queries._FM.
    Recalculée une fois par passage du watcher (au lieu d'à chaque requête)."""
    from src import db
    try:
        db.executer(SQL_MAP)
    except Exception as exc:  # noqa: BLE001
        log.warning("perf : table abn_secteur_actuel non rafraîchie : %s", exc)
        try:  # jamais absente : vide = repli sur le secteur de la facture
            db.executer("CREATE TABLE IF NOT EXISTS abn_secteur_actuel "
                        "(sa_abn BIGINT, STR_ID_ACT BIGINT, SECT_ID_ACT BIGINT)")
        except Exception:  # noqa: BLE001
            pass


def installer(app: FastAPI) -> None:
    """À appeler une fois, juste après la création de `app`."""
    # Ordre : GZip ajouté en dernier = couche la plus externe.
    app.add_middleware(PerfMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    # Chaque publication du watcher (= nouvelles données) vide le cache
    # AVANT d'avertir la page, qui recharge alors des données fraîches.
    global _app
    _app = app
    rafraichir_secteur_actuel()
    origine = sse.diffuseur.publier

    async def publier(evenement, donnees):
        await asyncio.to_thread(rafraichir_secteur_actuel)
        vider("sse " + str(evenement))
        _lancer_prechauffe()
        return await origine(evenement, donnees)

    sse.diffuseur.publier = publier

    @app.get("/api/perf", tags=["meta"])
    def api_perf() -> dict:
        return {"entrees": len(_cache), "octets": _octets,
                "ecrans_suivis": len(_recents), **stats}
