"""
Diffusion temps réel (Server-Sent Events).

Un gestionnaire garde une file par navigateur connecté. Le watcher publie,
tous les clients reçoivent. Le flux envoie un commentaire de maintien
toutes les 20 secondes, sans quoi un proxy ou un pare-feu finit par couper
une connexion inactive.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator


class Diffuseur:
    def __init__(self) -> None:
        self._files: list[asyncio.Queue] = []

    @property
    def nb_clients(self) -> int:
        return len(self._files)

    def connecter(self) -> asyncio.Queue:
        f: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._files.append(f)
        return f

    def deconnecter(self, f: asyncio.Queue) -> None:
        if f in self._files:
            self._files.remove(f)

    async def publier(self, evenement: str, donnees: dict[str, Any]) -> None:
        charge = json.dumps(donnees, ensure_ascii=False, default=str)
        message = f"event: {evenement}\ndata: {charge}\n\n"
        for f in list(self._files):
            try:
                f.put_nowait(message)
            except asyncio.QueueFull:
                # Client trop lent : on le laisse tomber plutôt que de
                # bloquer le watcher.
                self.deconnecter(f)

    async def flux(self, f: asyncio.Queue) -> AsyncIterator[str]:
        # Premier message immédiat : le bandeau sait qu'il est en ligne.
        yield "event: connecte\ndata: {\"ok\": true}\n\n"
        try:
            while True:
                try:
                    yield await asyncio.wait_for(f.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            self.deconnecter(f)


diffuseur = Diffuseur()
