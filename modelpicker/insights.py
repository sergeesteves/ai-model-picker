"""Phrases datées pour les zones dynamiques de la landing (gabarits, aucun LLM).

Chaque zone est un fragment HTML autonome (<p>…</p>) : il remplace l'intérieur d'un
<div class="cp-dyn" data-zone="…"> via l'ability creapulse-tools/update-dynamic-zones.
"""
from __future__ import annotations

import datetime as dt
from html import escape

from .match import ModelIndex
from .render import _dec
from .scoring import build_signals, build_usage, recommend

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre"]
FAMILIES = {"artificial_analysis": ("aa_",), "lmarena": ("lmarena_",), "epoch": ("epoch_",)}


def date_fr(iso: str | None) -> str:
    if not iso:
        return "date inconnue"
    y, m, d = (int(x) for x in iso[:10].split("-"))
    return f"{'1er' if d == 1 else d} {MOIS[m - 1]} {y}"


def _top_share(pct: float) -> str:
    """Percentile 0-100 -> « top 12 % » (plancher à 1 %)."""
    return f"top {max(1, round(100 - pct))} %"


def build_insights(models: dict, usage_history: list[dict], lmarena: dict | None, epoch: dict | None,
                   perf_history: list[dict] | None = None, today: str | None = None) -> dict:
    today = today or dt.date.today().isoformat()
    index = ModelIndex(models["data"])
    signals = build_signals(index, lmarena, epoch, models.get("source_date"))
    usage = build_usage(index, usage_history)
    name = {mid: (m.get("name") or mid).split(": ", 1)[-1] for mid, m in index.models.items()}

    # ---------------------------------------------------------------- zone 1 : l'écart entre les langues
    value_fr = recommend(models, usage_history, lmarena, epoch, {"task": "redaction_fr", "sort": "value", "top": 1},
                         today=today, perf_history=perf_history)["results"]
    quality_fr = recommend(models, usage_history, lmarena, epoch, {"task": "redaction_fr", "sort": "quality", "top": 1,
                                                                   "min_sources": 3},
                           today=today, perf_history=perf_history)["results"]
    fr = signals.get("lmarena_french", {}).get("scores", {})
    cw = signals.get("lmarena_creative_writing", {}).get("scores", {})
    # Le plus bel exemple de décrochage : très bon en rédaction, nettement moins bon en français
    # (parmi les modèles réellement utilisés : un vieux modèle délaissé ne parle à personne)
    used = {m for m, u in usage["by_model"].items() if u["rank"] <= 60}
    gaps = sorted(((cw[m]["pct"] - fr[m]["pct"], m) for m in set(fr) & set(cw) & used if cw[m]["pct"] >= 75),
                  reverse=True)
    parts = []
    if value_fr:
        parts.append(f"en rédaction en français, le meilleur rapport qualité-prix est "
                     f"<strong>{escape(value_fr[0]['name'])}</strong>")
    if quality_fr and (not value_fr or quality_fr[0]["id"] != value_fr[0]["id"]):
        parts.append(f"le mieux noté est <strong>{escape(quality_fr[0]['name'])}</strong>")
    sentence = f"À noter que l'écart entre les langues se voit. Au {date_fr(today)}, " + " et ".join(parts) + "."
    if gaps and gaps[0][0] >= 15:
        gap, mid = gaps[0]
        sentence += (f" À l'inverse, {escape(name[mid])} est dans le {_top_share(cw[mid]['pct'])} en rédaction, "
                     f"mais seulement dans le {_top_share(fr[mid]['pct'])} sur les prompts en français.")
    sentence += " Si vous publiez en français, ne vous fiez pas à un classement anglophone."

    # ---------------------------------------------------------------- zone 2 : fraîcheur et couverture
    compared = [mid for mid, m in index.models.items()
                if m.get("price_in") is not None and m["price_in"] >= 0 and "text" in (m.get("output_modalities") or [])
                and not (m["price_in"] == 0 and (m.get("price_out") or 0) == 0)]
    families: dict[str, set] = {}
    for sid, sig in signals.items():
        fam = next(f for f, prefixes in FAMILIES.items() if sid.startswith(prefixes))
        for mid in sig["scores"]:
            families.setdefault(mid, set()).add(fam)
    covered = [m for m in compared if families.get(m)]
    covered2 = [m for m in compared if len(families.get(m, ())) >= 2]

    top_usage = next(iter(usage["by_model"].items()), None)
    usage_line = ""
    if top_usage:
        mid, u = top_usage
        label = escape(name.get(mid, mid.split("/", 1)[-1]))
        n = len(families.get(mid, ()))
        state = ("n'est encore évalué par aucune source de qualité : il est signalé dans l'outil, pas classé"
                 if n == 0 else f"est noté par {n} source{'s' if n > 1 else ''} de qualité indépendante{'s' if n > 1 else ''} sur 3")
        usage_line = (f" Le modèle le plus utilisé la veille, <strong>{label}</strong> "
                      f"({_dec(u['tokens_per_day'] / 1e9, 0)} milliards de tokens), {state}.")
    lm_date = (lmarena or {}).get("source_date")
    ep_date = (epoch or {}).get("source_date")
    sources_line = (f"Au {date_fr(today)} : <strong>{len(compared)} modèles comparés</strong>, dont {len(covered)} notés par "
                    f"au moins une source de qualité et {len(covered2)} par au moins deux sources indépendantes. "
                    f"Prix et vitesses du {date_fr(models.get('source_date'))}, usage du {date_fr(usage.get('date'))}, "
                    f"LMArena du {date_fr(lm_date)}, Epoch AI du {date_fr(ep_date)}.{usage_line}")

    return {
        "date": today,
        "zones": {
            "qmia-langues": f"<p>{sentence}</p>",
            "qmia-sources": f"<p>{sources_line}</p>",
        },
        "facts": {
            "redaction_fr_value": value_fr[0]["id"] if value_fr else None,
            "redaction_fr_quality": quality_fr[0]["id"] if quality_fr else None,
            "language_gap": None if not gaps else {"id": gaps[0][1], "points": round(gaps[0][0], 1)},
            "compared": len(compared), "covered": len(covered), "covered_2_families": len(covered2),
            "top_usage": top_usage[0] if top_usage else None,
        },
    }
