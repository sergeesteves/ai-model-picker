"""Garde-fous en mémoire (1 worker) : débit par IP, plafonds quotidiens d'appels LLM, cache des questions."""
from __future__ import annotations

import datetime as dt
import re
import time
import unicodedata
from collections import OrderedDict, defaultdict, deque


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True


class DailyBudget:
    """Compteurs du jour : réservation avant l'appel LLM, restitution en cas d'échec."""

    def __init__(self, global_cap: int, ip_cap: int):
        self.global_cap, self.ip_cap = global_cap, ip_cap
        self.day = ""
        self.total = 0
        self.by_ip: dict[str, int] = defaultdict(int)

    def _roll(self) -> None:
        today = dt.date.today().isoformat()
        if today != self.day:
            self.day, self.total, self.by_ip = today, 0, defaultdict(int)

    def reserve(self, ip: str) -> str | None:
        """None si réservé, sinon le motif du refus : 'global_cap' ou 'ip_cap'."""
        self._roll()
        if self.total >= self.global_cap:
            return "global_cap"
        if self.by_ip[ip] >= self.ip_cap:
            return "ip_cap"
        self.total += 1
        self.by_ip[ip] += 1
        return None

    def release(self, ip: str) -> None:
        self._roll()
        self.total = max(0, self.total - 1)
        self.by_ip[ip] = max(0, self.by_ip[ip] - 1)

    @property
    def exhausted(self) -> bool:
        self._roll()
        return self.total >= self.global_cap


class QuestionCache:
    """Question normalisée -> requête validée. Une question déjà posée ne coûte pas un second appel."""

    def __init__(self, size: int):
        self.size = size
        self.data: OrderedDict[str, dict] = OrderedDict()

    @staticmethod
    def key(question: str) -> str:
        s = unicodedata.normalize("NFKD", question.lower())
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"[^a-z0-9]+", " ", s).strip()

    def get(self, question: str) -> dict | None:
        k = self.key(question)
        if k in self.data:
            self.data.move_to_end(k)
            return self.data[k]
        return None

    def put(self, question: str, value: dict) -> None:
        k = self.key(question)
        self.data[k] = value
        self.data.move_to_end(k)
        while len(self.data) > self.size:
            self.data.popitem(last=False)
