"""Rendu Markdown (terminal / skill) d'un résultat de scoring.recommend."""
from __future__ import annotations

from .scoring import SOURCE_SHORT

SORT_LABELS = {
    "value": "meilleur rapport qualité/prix",
    "quality": "meilleure qualité",
    "price": "moins cher",
    "usage": "plus utilisé",
    "fast": "meilleur compromis vitesse, qualité et prix",
}


def _dec(x: float, d: int = 2) -> str:
    return f"{x:,.{d}f}".replace(",", " ").replace(".", ",")


def _tokens(n: float) -> str:
    if n >= 1e9:
        return _dec(n / 1e9, 0 if n >= 1e11 else 1) + " Md"
    if n >= 1e6:
        return _dec(n / 1e6, 0) + " M"
    return _dec(n / 1e3, 0) + " k"


def _ctx(n) -> str:
    if not n:
        return "—"
    return f"{_dec(n / 1e6, 1).rstrip('0').rstrip(',')} M" if n >= 1e6 else f"{round(n / 1e3)} k"


def to_markdown(res: dict) -> str:
    q = res["query"]
    lines = []
    scope = [res["task_label"]]
    if q["authors"]:
        scope.append("éditeur : " + ", ".join(q["authors"]))
    if q["min_context"]:
        scope.append(f"contexte ≥ {_ctx(q['min_context'])}")
    if q["max_price"] is not None:
        scope.append(f"prix mixte ≤ {_dec(q['max_price'])} $/M")
    if q["open_weights"]:
        scope.append("poids ouverts")
    if q["input_modalities"]:
        scope.append("entrée " + " + ".join(q["input_modalities"]))
    if q["tools"]:
        scope.append("appel d'outils")
    if q.get("max_latency_ms") is not None:
        scope.append(f"latence ≤ {_dec(q['max_latency_ms'] / 1000, 1)} s")
    if q.get("min_throughput") is not None:
        scope.append(f"vitesse ≥ {_dec(q['min_throughput'], 0)} tokens/s")
    if q["min_sources"] > 1:
        scope.append(f"≥ {q['min_sources']} sources")
    if q["min_quality"]:
        scope.append(f"qualité ≥ {_dec(q['min_quality'], 0)}")
    lines.append(f"### {SORT_LABELS[q['sort']].capitalize()} · {' · '.join(scope)}")
    lines.append("")
    if not res["results"]:
        lines.append("_Aucun modèle ne passe ces filtres._")
    else:
        lines.append("| # | Modèle | Éditeur | Qualité (sources) | Prix mixte $/M | Entrée / sortie | Contexte | Latence | Vitesse | Usage/jour (rang) | Score qualité-prix /100 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in res["results"]:
            detail = ", ".join(f"{SOURCE_SHORT[s]} {_dec(v['percentile'], 0)}" for s, v in r["quality_detail"].items())
            u = r["usage"]
            usage = f"{_tokens(u['tokens_per_day'])} (#{u['rank']})" if u else "—"
            ow = " ⓞ" if r["open_weights"] else ""
            sp = r.get("speed") or {}
            lat = f"{_dec(sp['latency_ms'] / 1000, 1)} s" if sp.get("latency_ms") is not None else "—"
            tps = f"{_dec(sp['throughput_tps'], 0)} t/s" if sp.get("throughput_tps") is not None else "—"
            fp = sp.get("fastest_provider") or {}
            if fp.get("throughput_tps") and sp.get("throughput_tps") and fp["throughput_tps"] > sp["throughput_tps"] * 1.15:
                tps += f" (max {_dec(fp['throughput_tps'], 0)} chez {fp['provider']})"
            score = r["fast_index"] if q["sort"] == "fast" else r["value_index"]
            lines.append(
                f"| {r['position']} | {r['name']}{ow} | {r['author']} | **{_dec(r['quality'], 0)}** "
                f"({r['n_sources']}/{r['n_sources_possible']} : {detail}) | {_dec(r['blended_price_per_m'])} "
                f"| {_dec(r['price_in_per_m'])} / {_dec(r['price_out_per_m'])} | {_ctx(r['context_length'])} | {lat} | {tps} "
                f"| {usage} | {'—' if score is None else score} |")
    lines.append("")
    f = res["formula"]
    lines.append(f"**Formule** : score = {f['score']} avec α = {_dec(f['alpha'])}, β = {_dec(f['beta'])}. "
                 f"Q = {f['Q']} ; A = {f['A']} ; P = {f['P']}."
                 + (f" Tri rapide : {f['fast']}, R = {f['R']}." if "fast" in f else ""))
    lines.append(f"{f['index'].capitalize()} (en tri rapide : même principe sur le score rapide). "
                 f"{res['total_candidates']} modèles classés après filtres. ⓞ = poids ouverts (id Hugging Face déclaré).")
    lines.append("")
    lines.append("**Sources** :")
    for s in res["sources"]:
        lines.append(f"- {s['label']} — données du {s['date'] or '?'}, {s['matched']}/{s['entries']} entrées rapprochées d'un modèle OpenRouter")
    us = res["usage_source"]
    span = f"moyenne {us['first_date']} → {us['date']} ({us['days']} j)" if us["days"] > 1 else f"journée du {us['date']}"
    lines.append(f"- {us['label']} — {span}")
    lines.append(f"- {res['prices_source']['label']} — prix du {res['prices_source']['date']}")
    ss = res.get("speed_source") or {}
    if ss.get("days"):
        span = f"moyenne {ss['first_date']} → {ss['date']} ({ss['days']} j)" if ss["days"] > 1 else f"mesure du {ss['date']}"
        lines.append(f"- {ss['label']} — {span}")
    if res["unscored_popular"]:
        lines.append("")
        lines.append("**Très utilisés mais sans score de qualité pour cette tâche** (exclus du classement) : "
                     + ", ".join(f"{m['name']} (#{m['usage_rank']} usage)" for m in res["unscored_popular"]))
    for w in res.get("warnings") or []:
        lines.append("")
        lines.append(f"> ⚠️ {w}")
    lines.append("")
    for c in res["caveats"]:
        lines.append(f"_{c}_")
    return "\n".join(lines)
