"""Données en mémoire + rafraîchissement quotidien en tâche de fond (téléchargements seulement, aucun LLM)."""
from __future__ import annotations

import asyncio
import logging
import threading

from modelpicker import sources
from modelpicker.match import is_listed_model
from modelpicker.scoring import recommend

log = logging.getLogger("model-picker.data")


class DataStore:
    def __init__(self):
        self._lock = threading.Lock()
        self.models = self.lmarena = self.epoch = None
        self.history: list[dict] = []
        self.authors: set[str] = set()

    def reload(self) -> None:
        models = sources.load_latest("openrouter_models")
        with self._lock:
            self.models = models
            self.history = sources.load_usage_history(7)
            self.lmarena = sources.load_latest("lmarena")
            self.epoch = sources.load_latest("epoch")
            self.authors = {m["id"].split("/", 1)[0] for m in (models or {}).get("data", []) if is_listed_model(m)}

    @property
    def ready(self) -> bool:
        return self.models is not None

    def recommend(self, query: dict) -> dict:
        with self._lock:
            return recommend(self.models, self.history, self.lmarena, self.epoch, query)

    def refresh_blocking(self) -> None:
        missing = sources.needs_refresh()
        if missing:
            log.info("collecte : %s", ", ".join(missing))
            sources.collect(only=missing, log=log.info)
        self.reload()

    async def refresh_loop(self, interval_s: int, run_now: bool) -> None:
        first = True
        while True:
            if run_now or not first:
                try:
                    await asyncio.to_thread(self.refresh_blocking)
                except Exception as exc:  # une source en panne ne doit jamais tuer la boucle
                    log.error("rafraîchissement impossible : %s", exc)
            first = False
            await asyncio.sleep(interval_s)


store = DataStore()
