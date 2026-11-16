"""
Watcher — surveillance Oracle en tâche de fond.

Boucle, toutes les WATCHER_INTERVAL_SEC secondes (180 par défaut) :

  1. recharge CLOTURE_SECTEUR et la planification depuis Oracle
  2. relance les deux détections : baisses et retards de facturation
  3. publie en SSE s'il y a du nouveau

Au premier tick (dès le lancement de l'application), puis un tick sur
WATCHER_FACTURES_TOUS_LES (10 par défaut, soit une demi-heure), il
recharge aussi les factures et les relevés des cycles récents, puis
relance les détections : l'état « facturé » et l'avancement réel sont
à jour sans lancer `python -m src.etl` à la main.

Pourquoi 180 secondes et pas 10 comme le dashboard : ici on surveille le
VOLUME FACTURÉ, qui ne bouge qu'à la clôture et à la facturation d'un
secteur. Descendre sous la minute n'apporterait rien et chargerait Oracle
pour rien.

Le watcher démarre avec l'API. Mettre WATCHER_ACTIF=0 dans le .env pour
ouvrir l'interface sans toucher à Oracle.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from src import alerts, config, db, etl, sse

_tache: asyncio.Task | None = None

# État exposé par /api/etat, pour l'en-tête de l'interface.
etat: dict = {
    "actif": False,
    "dernier_tick": None,
    "derniere_erreur": None,
    "ticks": 0,
    "intervalle_sec": config.WATCHER_INTERVAL_SEC,
}


async def _boucle() -> None:
    etat["actif"] = True
    tick = 0

    while True:
        try:
            tick += 1
            etat["ticks"] = tick

            # 1 + 2. CLOTURE_SECTEUR, planification, puis détections.
            r = await asyncio.to_thread(etl.rapide)
            nouvelles = r.get("nouvelles_alertes", 0)
            nouv_retard = r.get("nouvelles_alertes_retard", 0)

            # Factures et relevés : au lancement, puis un tick sur N.
            # Les détections sont relancées ensuite, car c'est la
            # facturation qui décide des deux alertes.
            erreur_factures = None
            if tick == 1 or tick % max(config.WATCHER_FACTURES_TOUS_LES, 1) == 0:
                try:
                    r2 = await asyncio.to_thread(etl.factures_puis_detections)
                    nouvelles += r2.get("nouvelles_alertes", 0)
                    nouv_retard += r2.get("nouvelles_alertes_retard", 0)
                except Exception as exc:  # noqa: BLE001
                    # Non bloquant : le niveau global reste à jour même si
                    # le détail par référence n'a pas pu être rafraîchi.
                    erreur_factures = (
                        f"factures : {str(exc).splitlines()[0][:160]}"
                    )

            etat["dernier_tick"] = datetime.now()
            # Effacée seulement si le chargement des factures a réussi.
            etat["derniere_erreur"] = erreur_factures

            # 3. Diffusion — un événement par type d'alerte.
            if nouvelles > 0:
                c = await asyncio.to_thread(alerts.compter, True)
                await sse.diffuseur.publier("nouvelle_baisse", {
                    "nouvelles": nouvelles,
                    "total_non_lues": c["n"],
                    "volume_perdu": c["volume_perdu"],
                    "nb_centres": c["nb_centres"],
                    "detecte_a": datetime.now().isoformat(timespec="seconds"),
                })

            if nouv_retard > 0:
                cr = await asyncio.to_thread(alerts.compter_retards, True)
                await sse.diffuseur.publier("nouveau_retard", {
                    "nouvelles": nouv_retard,
                    "total_non_lues": cr["n"],
                    "retard_max": cr["retard_max"],
                    "nb_centres": cr["nb_centres"],
                    "detecte_a": datetime.now().isoformat(timespec="seconds"),
                })

            if nouvelles == 0 and nouv_retard == 0:
                await sse.diffuseur.publier("maj", {
                    "a": datetime.now().isoformat(timespec="seconds"),
                })

        except asyncio.CancelledError:
            break
        except Exception as exc:  # noqa: BLE001
            etat["derniere_erreur"] = str(exc).splitlines()[0][:200]
            etat["dernier_tick"] = datetime.now()
            await sse.diffuseur.publier("erreur", {
                "message": etat["derniere_erreur"],
            })
            # Oracle injoignable : les alertes sont tout de même recalculées
            # sur la base locale. Un retard dépend surtout de la date du
            # jour, il doit être signalé même sans Oracle.
            try:
                nb = await asyncio.to_thread(alerts.detecter)
                nbr = await asyncio.to_thread(alerts.detecter_retards)
                if nb > 0:
                    c = await asyncio.to_thread(alerts.compter, True)
                    await sse.diffuseur.publier("nouvelle_baisse", {
                        "nouvelles": nb, "total_non_lues": c["n"],
                        "volume_perdu": c["volume_perdu"],
                        "nb_centres": c["nb_centres"],
                    })
                if nbr > 0:
                    cr = await asyncio.to_thread(alerts.compter_retards, True)
                    await sse.diffuseur.publier("nouveau_retard", {
                        "nouvelles": nbr, "total_non_lues": cr["n"],
                        "retard_max": cr["retard_max"],
                        "nb_centres": cr["nb_centres"],
                    })
            except Exception:  # noqa: BLE001
                pass

        await asyncio.sleep(max(config.WATCHER_INTERVAL_SEC, 30))

    etat["actif"] = False


async def demarrer() -> None:
    """Lance le watcher si la configuration le permet."""
    global _tache

    if not config.WATCHER_ACTIF:
        etat["derniere_erreur"] = "watcher désactivé (WATCHER_ACTIF=0)"
        return
    if not config.oracle_configure():
        etat["derniere_erreur"] = (
            "identifiants Oracle absents du .env — l'interface fonctionne "
            "sur les données déjà chargées"
        )
        return

    # La base doit exister avant que l'interface n'interroge quoi que ce soit.
    try:
        await asyncio.to_thread(db.initialiser)
    except Exception as exc:  # noqa: BLE001
        etat["derniere_erreur"] = str(exc)[:200]
        return

    _tache = asyncio.create_task(_boucle())


async def arreter() -> None:
    global _tache
    if _tache is not None:
        _tache.cancel()
        try:
            await _tache
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _tache = None
    etat["actif"] = False
