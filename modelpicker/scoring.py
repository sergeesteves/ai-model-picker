"""Classement déterministe : qualité (percentiles multi-sources) × prix mixte × adoption.

Tout ce module est pur (aucun accès réseau ni disque hors data/tasks.json) : il sert tel quel au
CLI, au futur outil web et au futur serveur MCP. Entrée = instantanés + requête, sortie = dict JSON.
"""
from __future__ import annotations

import datetime as dt
import json
from bisect import bisect_left, bisect_right
from pathlib import Path

from .match import ModelIndex

TASKS_PATH = Path(__file__).resolve().parent.parent / "data" / "tasks.json"

SOURCE_LABELS = {
    "aa_intelligence": "Artificial Analysis · Intelligence Index (via API OpenRouter)",
    "aa_coding": "Artificial Analysis · Coding Index (via API OpenRouter)",
    "aa_agentic": "Artificial Analysis · Agentic Index (via API OpenRouter)",
    "lmarena_text": "LMArena · Text, préférence humaine (Elo)",
    "lmarena_coding": "LMArena · Text/Coding, préférence humaine (Elo)",
    "lmarena_webdev": "LMArena · WebDev, préférence humaine (Elo)",
    "lmarena_agent": "LMArena · Agent, résultats de sessions agentiques réelles (score)",
    "epoch_eci": "Epoch AI · Epoch Capabilities Index",
}
SOURCE_SHORT = {
    "aa_intelligence": "AA", "aa_coding": "AA", "aa_agentic": "AA",
    "lmarena_text": "LMArena", "lmarena_coding": "LMArena-code", "lmarena_webdev": "LMArena-webdev",
    "lmarena_agent": "LMArena-agent", "epoch_eci": "Epoch",
}
AA_FIELDS = {"aa_intelligence": "intelligence_index", "aa_coding": "coding_index", "aa_agentic": "agentic_index"}

DEFAULTS = {
    "task": "general",
    "sort": "value",           # value | quality | price | usage
    "top": 5,
    "authors": [],
    "min_context": None,
    "max_price": None,         # prix mixte max, $ par million de tokens
    "min_quality": None,       # None -> 60 pour sort=value, 0 sinon
    "min_sources": 1,
    "open_weights": False,
    "input_modalities": [],    # ex. ["image"]
    "tools": False,
    "input_share": None,       # None -> valeur du profil de tâche
    "price_weight": 0.5,       # β
    "adoption_weight": 0.25,   # α
    "include_free": False,
}
PRICE_FLOOR = 0.05  # $/M : évite qu'un prix quasi nul fasse exploser le score
PRIOR = 50.0        # a priori neutre, compte pour une source : un modèle noté par une seule source est tiré vers la médiane
VALUE_MIN_QUALITY = 60.0


def load_tasks() -> dict:
    raw = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


# ---------------------------------------------------------------- percentiles
def percentiles(scores: list[float]) -> list[float]:
    """Part (0-100) des autres entrées strictement moins bien notées, ex æquo comptés pour moitié."""
    n = len(scores)
    if n == 1:
        return [100.0]
    ordered = sorted(scores)
    out = []
    for s in scores:
        below = bisect_left(ordered, s)
        ties = bisect_right(ordered, s) - below
        out.append(100.0 * (below + 0.5 * (ties - 1)) / (n - 1))
    return out


# ---------------------------------------------------------------- signaux de qualité
def build_signals(index: ModelIndex, lmarena: dict | None, epoch: dict | None, models_date: str | None) -> dict:
    signals = {}
    for sid, field in AA_FIELDS.items():
        pop = [(mid, m["artificial_analysis"][field]) for mid, m in index.models.items()
               if (m.get("artificial_analysis") or {}).get(field) is not None]
        pcts = percentiles([s for _, s in pop])
        signals[sid] = {
            "date": models_date, "entries": len(pop), "matched": len(pop),
            "scores": {mid: {"pct": p, "raw": s, "as": mid} for (mid, s), p in zip(pop, pcts)},
        }
    boards = {}
    if lmarena:
        boards.update({k: ("lmarena", v) for k, v in lmarena["data"].items()})
    if epoch:
        boards.update({k: ("epoch", v) for k, v in epoch["data"].items()})
    for sid, (alias_ns, board) in boards.items():
        entries = board["entries"]
        pcts = percentiles([e["score"] for e in entries]) if entries else []
        scores, unmatched = {}, []
        for e, p in zip(entries, pcts):
            mid = index.resolve(e["name"], alias_ns)
            if mid is None and e.get("alt_name"):
                mid = index.resolve(e["alt_name"], alias_ns)
            if mid is None:
                unmatched.append((e["name"], p))
                continue
            if mid not in scores or p > scores[mid]["pct"]:  # meilleur niveau d'effort publié
                scores[mid] = {"pct": p, "raw": e["score"], "as": e["name"]}
        signals[sid] = {"date": board.get("date"), "entries": len(entries), "matched": len(scores),
                        "scores": scores, "unmatched": sorted(unmatched, key=lambda x: -x[1])}
    return signals


# ---------------------------------------------------------------- usage OpenRouter
def build_usage(index: ModelIndex, history: list[dict]) -> dict:
    """Moyenne des tokens/jour sur les instantanés disponibles (jour manquant pour un modèle = 0)."""
    if not history:
        return {"date": None, "days": 0, "total": 0, "by_model": {}, "unresolved": []}
    totals: dict[str, dict] = {}
    for snap in history:
        for slug, u in snap["data"].items():
            key = index.resolve_usage_slug(slug) or slug
            t = totals.setdefault(key, {"prompt": 0, "completion": 0, "requests": 0})
            t["prompt"] += u["prompt"]
            t["completion"] += u["completion"]
            t["requests"] += u["requests"]
    days = len(history)
    rows = []
    for key, t in totals.items():
        tokens = (t["prompt"] + t["completion"]) / days
        if tokens > 0:
            rows.append((key, tokens, t["prompt"] / (t["prompt"] + t["completion"])))
    rows.sort(key=lambda r: -r[1])
    total = sum(r[1] for r in rows)
    pcts = percentiles([r[1] for r in rows]) if rows else []
    by_model = {key: {"tokens_per_day": tok, "share": 100 * tok / total, "rank": i + 1, "pct": p,
                      "observed_input_share": ish}
                for i, ((key, tok, ish), p) in enumerate(zip(rows, pcts))}
    unresolved = [(key, v["rank"]) for key, v in by_model.items() if key not in index.models]
    dates = sorted(s["source_date"] for s in history)
    return {"date": dates[-1], "first_date": dates[0], "days": days, "total": total,
            "by_model": by_model, "unresolved": sorted(unresolved, key=lambda x: x[1])}


# ---------------------------------------------------------------- recommandation
def author_of(model_id: str) -> str:
    return model_id.split("/", 1)[0]


def recommend(models_snap: dict, usage_history: list[dict], lmarena_snap: dict | None,
              epoch_snap: dict | None, query: dict | None = None, today: str | None = None) -> dict:
    q = {**DEFAULTS, **{k: v for k, v in (query or {}).items() if v is not None}}
    tasks = load_tasks()
    if q["task"] not in tasks:
        raise ValueError(f"tâche inconnue « {q['task']} » (choix : {', '.join(tasks)})")
    task = tasks[q["task"]]
    if q["sort"] not in ("value", "quality", "price", "usage"):
        raise ValueError("sort doit valoir value, quality, price ou usage")
    min_quality = q["min_quality"] if q["min_quality"] is not None else (VALUE_MIN_QUALITY if q["sort"] == "value" else 0.0)
    share_in = q["input_share"] if q["input_share"] is not None else task["input_share"]
    alpha, beta = q["adoption_weight"], q["price_weight"]
    authors = {a.lower() for a in q["authors"]}
    today = today or dt.date.today().isoformat()

    index = ModelIndex(models_snap["data"])
    signals = build_signals(index, lmarena_snap, epoch_snap, models_snap.get("source_date"))
    usage = build_usage(index, usage_history)
    task_sources = [s for s in task["sources"] if s in signals]

    results, unscored_popular = [], []
    for mid, m in index.models.items():
        pin, pout = m.get("price_in"), m.get("price_out")
        if pin is None or pout is None or pin < 0 or pout < 0:
            continue  # routeurs (openrouter/auto…) : prix non défini
        if "text" not in (m.get("output_modalities") or []):
            continue
        if m.get("expiration_date") and str(m["expiration_date"])[:10] < today:
            continue
        free = pin == 0 and pout == 0
        if free and not q["include_free"]:
            continue
        if authors and author_of(mid) not in authors:
            continue
        if q["min_context"] and (m.get("context_length") or 0) < q["min_context"]:
            continue
        if q["open_weights"] and not m.get("hugging_face_id"):
            continue
        if q["tools"] and "tools" not in (m.get("supported_parameters") or []):
            continue
        if any(x not in (m.get("input_modalities") or []) for x in q["input_modalities"]):
            continue
        blended = (share_in * pin + (1 - share_in) * pout) * 1e6
        if q["max_price"] is not None and blended > q["max_price"]:
            continue

        found = {s: signals[s]["scores"][mid] for s in task_sources if mid in signals[s]["scores"]}
        u = usage["by_model"].get(mid)
        if not found:
            if u and u["rank"] <= 25:
                unscored_popular.append({"id": mid, "name": _display_name(m), "usage_rank": u["rank"]})
            continue
        quality = (sum(v["pct"] for v in found.values()) + PRIOR) / (len(found) + 1)
        if len(found) < q["min_sources"] or quality < min_quality:
            continue
        adoption = u["pct"] if u else 0.0
        score = quality * (1 + alpha * adoption / 100) / max(blended, PRICE_FLOOR) ** beta
        results.append({
            "id": mid, "name": _display_name(m), "author": author_of(mid),
            "quality": round(quality, 1), "n_sources": len(found), "n_sources_possible": len(task_sources),
            "quality_detail": {s: {"percentile": round(v["pct"], 1), "raw": v["raw"], "matched_as": v["as"]}
                               for s, v in found.items()},
            "price_in_per_m": round(pin * 1e6, 4), "price_out_per_m": round(pout * 1e6, 4),
            "blended_price_per_m": round(blended, 4),
            "context_length": m.get("context_length"),
            "open_weights": bool(m.get("hugging_face_id")),
            "usage": None if not u else {
                "tokens_per_day": round(u["tokens_per_day"]), "share_pct": round(u["share"], 2),
                "rank": u["rank"], "percentile": round(u["pct"], 1),
                "observed_input_share": round(u["observed_input_share"], 3)},
            "adoption": round(adoption, 1),
            "score": round(score, 2),
        })

    sort_keys = {
        "value": lambda r: (-r["score"], r["blended_price_per_m"]),
        "quality": lambda r: (-r["quality"], r["blended_price_per_m"]),
        "price": lambda r: (r["blended_price_per_m"], -r["quality"]),
        "usage": lambda r: (-(r["usage"] or {}).get("tokens_per_day", 0), -r["quality"]),
    }
    results.sort(key=sort_keys[q["sort"]])
    for i, r in enumerate(results):
        r["position"] = i + 1

    return {
        "query": {**q, "min_quality": min_quality, "input_share": share_in},
        "task_label": task["label"],
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "formula": {
            "score": "Q × (1 + α × A / 100) ÷ max(P, 0,05)^β",
            "Q": "(somme des percentiles 0-100 du modèle dans les n sources de qualité de la tâche + 50) ÷ (n + 1)",
            "A": "percentile d'usage OpenRouter (tokens/jour), 0 si absent du classement",
            "P": f"prix mixte $/M tokens = {round(share_in * 100)} % × entrée + {round((1 - share_in) * 100)} % × sortie",
            "alpha": alpha, "beta": beta,
        },
        "sources": [{"id": s, "label": SOURCE_LABELS[s], "date": signals[s]["date"],
                     "entries": signals[s]["entries"], "matched": signals[s]["matched"]} for s in task_sources],
        "usage_source": {"label": "OpenRouter · Rankings (tokens traités, tous hébergeurs)",
                         "date": usage["date"], "first_date": usage.get("first_date"), "days": usage["days"]},
        "prices_source": {"label": "OpenRouter · /api/v1/models", "date": models_snap.get("source_date")},
        "total_candidates": len(results),
        "results": results[: q["top"]],
        "unscored_popular": sorted(unscored_popular, key=lambda x: x["usage_rank"])[:5],
        "caveats": [
            "Usage = trafic développeurs via l'API OpenRouter, pas le grand public ; OpenAI, Anthropic et Google y sont sous-représentés par rapport à leurs canaux directs, et les modèles économiques dominent mécaniquement le volume.",
            "Qualité en percentiles : un rang, pas un écart. LMArena retient le meilleur niveau d'effort publié (ex. « max »), plus coûteux en tokens de réflexion que le prix affiché ne le laisse penser.",
        ],
    }


def _display_name(m: dict) -> str:
    name = m.get("name") or m["id"]
    return name.split(": ", 1)[1] if ": " in name else name
