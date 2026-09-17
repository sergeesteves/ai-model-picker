"""Configuration de l'app web — tout vient des variables d'environnement."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw in (None, "") else raw.strip().lower() in ("1", "true", "yes", "on")


def _root_path() -> str:
    raw = os.getenv("ROOT_PATH", "").strip("/")
    return "/" + raw if raw else ""


@dataclass
class Settings:
    root_path: str = _root_path()
    debug: bool = _bool("DEBUG", False)
    trust_proxy_headers: bool = _bool("TRUST_PROXY_HEADERS", True)

    # LLM (OmniRoute, API compatible OpenAI) : uniquement pour traduire une question libre en filtres
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://omniroute.creapulse.fr/api/v1").rstrip("/")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "mistralai/mistral-small-3.2-24b-instruct")
    llm_timeout_s: float = float(os.getenv("LLM_TIMEOUT_S") or 20)

    # Garde-fous (en mémoire, remis à zéro chaque jour ; le plafond budgétaire dur reste celui d'OmniRoute)
    llm_global_daily_cap: int = _int("LLM_GLOBAL_DAILY_CAP", 300)
    llm_ip_daily_cap: int = _int("LLM_IP_DAILY_CAP", 10)
    ip_rate_limit_per_min: int = _int("IP_RATE_LIMIT_PER_MIN", 20)
    question_max_chars: int = _int("QUESTION_MAX_CHARS", 300)
    ask_cache_size: int = _int("ASK_CACHE_SIZE", 500)

    # Collecte : vérifie toutes les heures s'il manque les données du jour (aucun LLM)
    refresh_interval_s: int = _int("REFRESH_INTERVAL_S", 3600)
    collect_on_startup: bool = _bool("COLLECT_ON_STARTUP", True)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)


settings = Settings()
