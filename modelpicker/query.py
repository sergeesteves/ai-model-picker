"""Validation d'une requête venue de l'extérieur (formulaire web, LLM, MCP) : liste blanche stricte.

Toute valeur hors liste ou hors bornes est ignorée (retour au défaut), jamais transmise telle quelle.
Les paramètres de la formule (α, β, part d'entrée) ne sont pas exposés : la formule publique reste fixe.
"""
from __future__ import annotations

from .render import _ctx, _dec
from .scoring import SORTS, load_tasks

MODALITIES = ("image", "file", "audio", "video")


def _num(v, lo, hi, cast=float):
    try:
        x = cast(v)
    except (TypeError, ValueError):
        return None
    return x if lo <= x <= hi else None


def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on", "oui")


def _list(v) -> list[str]:
    if v is None:
        return []
    items = v if isinstance(v, (list, tuple)) else str(v).split(",")
    return [str(x).strip().lower() for x in items if str(x).strip()]


def sanitize_query(raw: dict, known_authors: set[str], max_top: int = 10) -> dict:
    raw = raw or {}
    tasks = load_tasks()
    q = {
        "task": raw.get("task") if raw.get("task") in tasks else "general",
        "sort": raw.get("sort") if raw.get("sort") in SORTS else "value",
        "authors": [a for a in _list(raw.get("authors")) if a in known_authors][:5],
        "min_context": _num(raw.get("min_context"), 1_000, 20_000_000, int),
        "max_price": _num(raw.get("max_price"), 0.001, 1_000),
        "min_quality": _num(raw.get("min_quality"), 0, 100),
        "min_sources": _num(raw.get("min_sources"), 1, 3, int) or 1,
        "open_weights": _bool(raw.get("open_weights", False)),
        "tools": _bool(raw.get("tools", False)),
        "input_modalities": [m for m in _list(raw.get("input_modalities")) if m in MODALITIES],
        "top": _num(raw.get("top"), 1, max_top, int) or 5,
        "max_latency_ms": _num(raw.get("max_latency_ms"), 50, 120_000, int),
        "min_throughput": _num(raw.get("min_throughput"), 1, 5_000),
    }
    return q


def describe_query(q: dict) -> list[str]:
    """Libellés lisibles de la requête, pour afficher comment une question a été comprise."""
    tasks = load_tasks()
    sort_labels = {"value": "meilleur rapport qualité/prix", "quality": "meilleure qualité",
                   "price": "le moins cher", "usage": "le plus utilisé", "fast": "rapide et bon rapport qualité/prix"}
    out = [tasks[q["task"]]["label"], sort_labels[q["sort"]]]
    if q["authors"]:
        out.append("éditeur : " + ", ".join(q["authors"]))
    if q["min_context"]:
        out.append(f"contexte ≥ {_ctx(q['min_context'])}")
    if q["max_price"] is not None:
        out.append(f"prix mixte ≤ {_dec(q['max_price'])} $/M")
    if q["min_quality"] is not None:
        out.append(f"qualité ≥ {_dec(q['min_quality'], 0)}")
    if q["min_sources"] > 1:
        out.append(f"≥ {q['min_sources']} sources")
    if q["open_weights"]:
        out.append("poids ouverts")
    if q["tools"]:
        out.append("appel d'outils")
    if q["input_modalities"]:
        out.append("entrée " + " + ".join(q["input_modalities"]))
    if q.get("max_latency_ms") is not None:
        out.append(f"latence ≤ {_dec(q['max_latency_ms'] / 1000, 1)} s")
    if q.get("min_throughput") is not None:
        out.append(f"vitesse ≥ {_dec(q['min_throughput'], 0)} tokens/s")
    return out


def summary_sentence(res: dict) -> str:
    """Phrase de synthèse générée par gabarit (aucun LLM) à partir du résultat."""
    rows = res.get("results") or []
    if not rows:
        return "Aucun modèle ne passe ces filtres. Essayez d'en relâcher un."
    r = rows[0]
    usage = f", {r['usage']['rank']}ᵉ modèle le plus utilisé sur OpenRouter" if r.get("usage") else ""
    sort = res["query"]["sort"]
    head = {
        "value": "Meilleur rapport qualité/prix",
        "quality": "Meilleure qualité",
        "price": "Le moins cher",
        "usage": "Le plus utilisé",
        "fast": "Meilleur compromis vitesse, qualité et prix",
    }[sort]
    sp = r.get("speed") or {}
    speed = ""
    if sp.get("latency_ms") is not None and sp.get("throughput_tps") is not None:
        speed = f", premier token en {_dec(sp['latency_ms'] / 1000, 1)} s et {_dec(sp['throughput_tps'], 0)} tokens/s"
    return (f"{head} pour « {res['task_label']} » : {r['name']} ({r['author']}), "
            f"qualité {_dec(r['quality'], 0)}/100 sur {r['n_sources']} source{'s' if r['n_sources'] > 1 else ''}, "
            f"{_dec(r['blended_price_per_m'])} $ le million de tokens en prix mixte{speed}{usage}.")
