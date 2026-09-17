"""Rapprochement des noms de modèles entre sources (LMArena, Epoch) et ids OpenRouter.

Règle : on normalise (minuscules, séparateurs -> '-', sans préfixe éditeur ni parenthèses), on essaie
d'abord la clé exacte, puis la clé sans suffixe d'effort/réflexion/date. Les cas qui résistent se
règlent dans data/aliases.json, jamais dans le code.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ALIASES_PATH = Path(__file__).resolve().parent.parent / "data" / "aliases.json"

# Jetons de variante retirés où qu'ils soient (effort, budget de réflexion, quantization, harnais, date)
_VARIANT_TOKENS = re.compile(
    r"^(?:xhigh|high|medium|low|minimal|thinking|non-thinking|nothinking|reasoning|instant|"
    r"codex-harness|nvfp4|fp8|bf16|\d+k|\d{8})$")
_TRAILING_ONLY = re.compile(r"-(?:max|\d{4}-\d{2}-\d{2})$")  # « max » peut faire partie du nom (qwen3.8-max)


def normalize(name: str) -> str:
    s = (name or "").lower().strip()
    s = re.sub(r"^[^/]*/", "", s)          # préfixe éditeur d'un slug (openai/…)
    s = re.sub(r"^[^:]{1,40}:\s+", "", s)  # préfixe « Éditeur: » du champ name d'OpenRouter
    s = re.sub(r"\(([^)]*)\)", r" \1", s)  # « (High) » -> « high » (retiré ensuite comme effort)
    s = re.sub(r"[\s_.]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    return re.sub(r"-{2,}", "-", s).strip("-")


def strip_variant(key: str) -> str:
    key = re.sub(r"-(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)-\d{4}(?=-|$)", "", key)  # « (Jul 2025) »
    for tok in ("non-thinking", "codex-harness"):  # jetons composés, avant découpage
        key = key.replace(tok, tok.replace("-", "~"))
    parts = [p for p in key.split("-") if not _VARIANT_TOKENS.match(p.replace("~", "-"))]
    key = "-".join(parts).replace("~", "-")
    prev = None
    while prev != key:
        prev, key = key, _TRAILING_ONLY.sub("", key)
    return key


def compact(key: str) -> str:
    return key.replace("-", "")


def is_listed_model(m: dict) -> bool:
    """Modèle « standard » : ni variante (:free, :batch…), ni alias glissant (~openai/…-latest)."""
    mid = m.get("id") or ""
    return bool(mid) and ":" not in mid and not mid.startswith("~")


class ModelIndex:
    def __init__(self, models: list[dict]):
        self.models = {m["id"]: m for m in models if is_listed_model(m)}
        self.by_canon = {}
        for m in self.models.values():
            if m.get("canonical_slug"):
                self.by_canon.setdefault(m["canonical_slug"], m["id"])
        self._keys: dict[str, str] = {}
        ordered = sorted(self.models.values(), key=lambda m: -(m.get("created") or 0))
        # passe 1 : clés exactes (id, nom) ; passe 2 : clés dérivées, seulement si libres
        for derive in (False, True):
            for m in ordered:
                for raw in (m["id"], m.get("name") or ""):
                    key = normalize(raw)
                    if derive:
                        key = strip_variant(key)
                    if key and key not in self._keys:
                        self._keys[key] = m["id"]
        # passe 3 : clés compactes sans tirets (« qwen-3-8-27b » = « qwen3-8-27b »), si non ambiguës
        compacts: dict[str, set] = {}
        for key, mid in self._keys.items():
            compacts.setdefault(compact(key), set()).add(mid)
        self._compact = {k: next(iter(v)) for k, v in compacts.items() if len(v) == 1}
        self.aliases = _load_aliases()

    def resolve(self, name: str, source: str) -> str | None:
        alias = self.aliases.get(source, {}).get(name)
        if alias is not None:
            return alias if alias in self.models else None  # alias vers "" = exclusion volontaire
        key = normalize(name)
        base = strip_variant(key)
        return (self._keys.get(key) or self._keys.get(base)
                or self._compact.get(compact(key)) or self._compact.get(compact(base)))

    def resolve_usage_slug(self, permaslug: str) -> str | None:
        if permaslug in self.by_canon:
            return self.by_canon[permaslug]
        base = re.sub(r"-\d{8}$", "", permaslug)
        if base in self.models:
            return base
        return self.by_canon.get(base)


def _load_aliases() -> dict:
    if not ALIASES_PATH.exists():
        return {}
    raw = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}
