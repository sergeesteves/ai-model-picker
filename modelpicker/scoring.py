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
    "aa_intelligence": "Artificial Analysis · Intelligence Index (relayé par OpenRouter)",
    "aa_coding": "Artificial Analysis · Coding Index (relayé par OpenRouter)",
    "aa_agentic": "Artificial Analysis · Agentic Index (relayé par OpenRouter)",
    "lmarena_text": "LMArena · Text, préférence humaine (Elo)",
    "lmarena_coding": "LMArena · Text/Coding, préférence humaine (Elo)",
    "lmarena_creative_writing": "LMArena · Rédaction créative, préférence humaine (Elo)",
    "lmarena_instruction_following": "LMArena · Respect des consignes, préférence humaine (Elo)",
    "lmarena_french": "LMArena · Prompts en français, préférence humaine (Elo)",
    "lmarena_longer_query": "LMArena · Requêtes longues, préférence humaine (Elo)",
    "lmarena_webdev": "LMArena · WebDev, préférence humaine (Elo)",
    "lmarena_agent": "LMArena · Agent, résultats de sessions agentiques réelles (score)",
    "epoch_eci": "Epoch AI · Epoch Capabilities Index",
}
SOURCE_SHORT = {
    "aa_intelligence": "AA", "aa_coding": "AA", "aa_agentic": "AA",
    "lmarena_text": "LMArena", "lmarena_coding": "LMArena-code", "lmarena_webdev": "LMArena-webdev",
    "lmarena_creative_writing": "LMArena-rédac", "lmarena_instruction_following": "LMArena-consignes",
    "lmarena_french": "LMArena-fr", "lmarena_longer_query": "LMArena-long",
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
    "max_latency_ms": None,    # latence médiane avant le premier token, en ms
    "min_throughput": None,    # débit médian minimum, en tokens/s
}
PRICE_FLOOR = 0.05  # $/M : évite qu'un prix quasi nul fasse exploser le score
PRIOR = 50.0        # a priori neutre, compte pour une source : un modèle noté par une seule source est tiré vers la médiane
VALUE_MIN_QUALITY = 60.0   # tris value et fast
SORTS = ("value", "quality", "price", "usage", "fast")
PERF_MIN_REQUESTS = 10     # en dessous, un hébergeur n'a pas assez de requêtes pour une médiane fiable


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


# ---------------------------------------------------------------- vitesse et latence (OpenRouter)
def _weighted(endpoints: list[dict], field: str) -> float | None:
    pts = [(e[field], e["requests"]) for e in endpoints if e.get(field) is not None and e["requests"] > 0]
    total = sum(w for _, w in pts)
    return sum(v * w for v, w in pts) / total if total else None


def build_perf(history: list[dict]) -> dict:
    """Par modèle : latence et débit médians, moyennés par requêtes sur les hébergeurs, puis sur les jours.

    Un hébergeur compte s'il est en service (status 0) avec au moins PERF_MIN_REQUESTS requêtes sur la fenêtre ;
    à défaut, tous ceux qui ont servi des requêtes. R = réactivité 0-100 = moyenne des percentiles de débit
    (plus haut = mieux) et de latence (plus bas = mieux) parmi les modèles mesurés.
    """
    if not history:
        return {"date": None, "days": 0, "by_model": {}}
    daily: dict[str, list[tuple]] = {}
    for snap in history:
        for mid, endpoints in snap["data"].items():
            usable = [e for e in endpoints if e.get("status") == 0 and e["requests"] >= PERF_MIN_REQUESTS] \
                or [e for e in endpoints if e["requests"] > 0]
            if not usable:
                continue
            lat, tps = _weighted(usable, "latency_ms"), _weighted(usable, "throughput_tps")
            best = max((e for e in usable if e.get("throughput_tps") is not None),
                       key=lambda e: e["throughput_tps"], default=None)
            daily.setdefault(mid, []).append((lat, tps, sum(e["requests"] for e in usable), best))
    by_model = {}
    for mid, days in daily.items():
        lats = [d[0] for d in days if d[0] is not None]
        tpss = [d[1] for d in days if d[1] is not None]
        best = days[0][3]  # instantané le plus récent
        by_model[mid] = {
            "latency_ms": sum(lats) / len(lats) if lats else None,
            "throughput_tps": sum(tpss) / len(tpss) if tpss else None,
            "requests": sum(d[2] for d in days), "days": len(days),
            "fastest_provider": None if not best else {"provider": best["provider"], "throughput_tps": best["throughput_tps"],
                                                       "latency_ms": best["latency_ms"]},
        }
    lat_ids = [k for k, v in by_model.items() if v["latency_ms"] is not None]
    tps_ids = [k for k, v in by_model.items() if v["throughput_tps"] is not None]
    lat_pct = dict(zip(lat_ids, percentiles([-by_model[k]["latency_ms"] for k in lat_ids]))) if lat_ids else {}
    tps_pct = dict(zip(tps_ids, percentiles([by_model[k]["throughput_tps"] for k in tps_ids]))) if tps_ids else {}
    for k, v in by_model.items():
        parts = [x for x in (lat_pct.get(k), tps_pct.get(k)) if x is not None]
        v["responsiveness"] = sum(parts) / len(parts) if parts else None
    dates = sorted(snap["source_date"] for snap in history)
    return {"date": dates[-1], "first_date": dates[0], "days": len(history),
            "measured_at": history[0].get("measured_at"), "by_model": by_model}


# ---------------------------------------------------------------- recommandation
def author_of(model_id: str) -> str:
    return model_id.split("/", 1)[0]


def recommend(models_snap: dict, usage_history: list[dict], lmarena_snap: dict | None,
              epoch_snap: dict | None, query: dict | None = None, today: str | None = None,
              perf_history: list[dict] | None = None) -> dict:
    q = {**DEFAULTS, **{k: v for k, v in (query or {}).items() if v is not None}}
    tasks = load_tasks()
    if q["task"] not in tasks:
        raise ValueError(f"tâche inconnue « {q['task']} » (choix : {', '.join(tasks)})")
    task = tasks[q["task"]]
    if q["sort"] not in SORTS:
        raise ValueError("sort doit valoir " + ", ".join(SORTS))
    min_quality = q["min_quality"] if q["min_quality"] is not None else (
        VALUE_MIN_QUALITY if q["sort"] in ("value", "fast") else 0.0)
    share_in = q["input_share"] if q["input_share"] is not None else task["input_share"]
    alpha, beta = q["adoption_weight"], q["price_weight"]
    authors = {a.lower() for a in q["authors"]}
    today = today or dt.date.today().isoformat()

    index = ModelIndex(models_snap["data"])
    signals = build_signals(index, lmarena_snap, epoch_snap, models_snap.get("source_date"))
    usage = build_usage(index, usage_history)
    perf = build_perf(perf_history or [])
    needs_perf = q["sort"] == "fast" or q["max_latency_ms"] is not None or q["min_throughput"] is not None
    task_sources = [s for s in task["sources"] if s in signals]
    warnings = []
    if not task_sources:
        warnings.append(f"Aucune source de qualité disponible pour « {task['label']} » : classements à collecter "
                        "(python -m modelpicker collect --only lmarena --force). Aucun modèle ne peut être classé.")
    elif len(task_sources) < len(task["sources"]):
        missing = ", ".join(SOURCE_LABELS[s] for s in task["sources"] if s not in signals)
        warnings.append(f"Sources absentes du cache pour cette tâche : {missing}.")

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

        sp = perf["by_model"].get(mid)
        if needs_perf and not (sp and sp["responsiveness"] is not None):
            continue  # vitesse demandée mais non mesurée : on n'invente rien
        if q["max_latency_ms"] is not None and (sp["latency_ms"] is None or sp["latency_ms"] > q["max_latency_ms"]):
            continue
        if q["min_throughput"] is not None and (sp["throughput_tps"] is None or sp["throughput_tps"] < q["min_throughput"]):
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
        fast_score = score * (0.5 + sp["responsiveness"] / 100) if sp and sp["responsiveness"] is not None else None
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
            "speed": None if not sp else {
                "latency_ms": None if sp["latency_ms"] is None else round(sp["latency_ms"]),
                "throughput_tps": None if sp["throughput_tps"] is None else round(sp["throughput_tps"], 1),
                "responsiveness": None if sp["responsiveness"] is None else round(sp["responsiveness"], 1),
                "requests": sp["requests"], "days": sp["days"], "fastest_provider": sp["fastest_provider"]},
            "fast_score": None if fast_score is None else round(fast_score, 2),
        })

    sort_keys = {
        "value": lambda r: (-r["score"], r["blended_price_per_m"]),
        "quality": lambda r: (-r["quality"], r["blended_price_per_m"]),
        "price": lambda r: (r["blended_price_per_m"], -r["quality"]),
        "usage": lambda r: (-(r["usage"] or {}).get("tokens_per_day", 0), -r["quality"]),
        "fast": lambda r: (-(r["fast_score"] or 0), r["blended_price_per_m"]),
    }
    # Indice sur 100 : 100 = meilleur modèle parmi ceux qui passent les filtres (avant troncature au top N)
    best_value = max((r["score"] for r in results), default=0)
    best_fast = max((r["fast_score"] or 0 for r in results), default=0)
    for r in results:
        r["value_index"] = round(100 * r["score"] / best_value) if best_value else None
        r["fast_index"] = round(100 * r["fast_score"] / best_fast) if best_fast and r["fast_score"] is not None else None
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
            "P_note": (f"répartition moyenne estimée pour l'usage « {task['label']} » : "
                       "elle peut varier selon votre utilisation"),
            "alpha": alpha, "beta": beta,
            "index": "score qualité-prix sur 100 = score ÷ meilleur score parmi les modèles qui passent les filtres × 100",
            **({"fast": "score × (0,5 + R / 100)",
                "R": "réactivité 0-100 = moyenne des percentiles de débit (tokens/s, plus haut = mieux) et de latence avant le premier token (plus bas = mieux), parmi les modèles mesurés"}
               if q["sort"] == "fast" else {}),
        },
        "sources": [{"id": s, "label": SOURCE_LABELS[s], "date": signals[s]["date"],
                     "entries": signals[s]["entries"], "matched": signals[s]["matched"]} for s in task_sources],
        "usage_source": {"label": "OpenRouter · tokens traités par modèle, tous hébergeurs",
                         "date": usage["date"], "first_date": usage.get("first_date"), "days": usage["days"]},
        "prices_source": {"label": "OpenRouter · tarifs publiés", "date": models_snap.get("source_date")},
        "speed_source": {"label": "OpenRouter · performances par hébergeur (médianes sur 30 min, pondérées par requêtes)",
                         "date": perf["date"], "first_date": perf.get("first_date"), "days": perf["days"],
                         "measured_at": perf.get("measured_at")},
        "warnings": warnings,
        "total_candidates": len(results),
        "results": results[: q["top"]],
        "unscored_popular": sorted(unscored_popular, key=lambda x: x["usage_rank"])[:5],
        "caveats": [
            "Usage = trafic développeurs via l'API OpenRouter, pas le grand public ; OpenAI, Anthropic et Google y sont sous-représentés par rapport à leurs canaux directs, et les modèles économiques dominent mécaniquement le volume.",
            "Vitesse = médianes mesurées par OpenRouter sur 30 minutes au moment de la collecte, pondérées par les requêtes de chaque hébergeur : elle varie selon l'heure, l'hébergeur choisi et la longueur des réponses. La latence d'un modèle qui réfléchit inclut souvent sa réflexion.",
            "Qualité en percentiles : un rang, pas un écart. LMArena retient le meilleur niveau d'effort publié (ex. « max »), plus coûteux en tokens de réflexion que le prix affiché ne le laisse penser.",
        ],
    }


def _display_name(m: dict) -> str:
    name = m.get("name") or m["id"]
    return name.split(": ", 1)[1] if ": " in name else name
