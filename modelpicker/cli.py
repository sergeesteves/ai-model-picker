"""Interface en ligne de commande : collect | recommend | coverage."""
from __future__ import annotations

import argparse
import json
import sys

from . import sources
from .match import ModelIndex
from .render import to_markdown
from .scoring import build_signals, build_usage, load_tasks, recommend


def load_context(offline: bool, log) -> tuple:
    missing = sources.needs_refresh()
    if missing and not offline:
        log(f"Collecte du jour ({', '.join(missing)})…")
        sources.collect(only=missing, log=log)
    models = sources.load_latest("openrouter_models")
    if not models:
        raise SystemExit("Aucune donnée OpenRouter en cache : lancer `python -m modelpicker collect` avec accès réseau.")
    return (models, sources.load_usage_history(7), sources.load_latest("lmarena"), sources.load_latest("epoch"),
            sources.load_history("openrouter_perf", 7))


def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in v.split(",") if x.strip()] if v else []


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    log = lambda msg: print(msg, file=sys.stderr)  # noqa: E731

    p = argparse.ArgumentParser(prog="modelpicker", description="Quel modèle IA choisir : qualité × prix × usage réel.")
    sub = p.add_subparsers(dest="cmd")

    c = sub.add_parser("collect", help="télécharger les sources du jour dans le cache")
    c.add_argument("--force", action="store_true", help="re-télécharger même si déjà en cache")
    c.add_argument("--only", help="sources, séparées par des virgules : " + ",".join(sources.SOURCES))

    r = sub.add_parser("recommend", help="classer les modèles")
    r.add_argument("--task", choices=list(load_tasks()), default="general")
    r.add_argument("--sort", choices=["value", "quality", "price", "usage", "fast"], default="value",
                   help="fast = rapport qualité/prix pondéré par la réactivité (latence + débit)")
    r.add_argument("--author", help="éditeur(s) = préfixe du slug OpenRouter, ex. openai,anthropic")
    r.add_argument("--min-context", type=int, help="contexte minimum en tokens, ex. 1000000")
    r.add_argument("--max-price", type=float, help="prix mixte maximum en $ par million de tokens")
    r.add_argument("--min-quality", type=float, help="qualité minimum 0-100 (défaut : 50 pour value, 0 sinon)")
    r.add_argument("--min-sources", type=int, default=1, help="nombre minimum de sources de qualité")
    r.add_argument("--open-weights", action="store_true", help="poids ouverts uniquement")
    r.add_argument("--input", help="modalités d'entrée requises, ex. image,file")
    r.add_argument("--tools", action="store_true", help="appel d'outils requis")
    r.add_argument("--max-latency", type=float, help="latence médiane max avant le premier token, en secondes")
    r.add_argument("--min-speed", type=float, help="débit médian minimum, en tokens/s")
    r.add_argument("--input-share", type=float, help="part des tokens d'entrée dans le prix mixte (0-1)")
    r.add_argument("--price-weight", type=float, default=0.5, help="β, poids du prix (défaut 0,5)")
    r.add_argument("--adoption-weight", type=float, default=0.25, help="α, bonus d'adoption (défaut 0,25)")
    r.add_argument("--include-free", action="store_true", help="inclure les modèles à 0 $")
    r.add_argument("--top", type=int, default=5)
    r.add_argument("--format", choices=["md", "json"], default="md")
    r.add_argument("--offline", action="store_true", help="ne jamais télécharger, utiliser le cache tel quel")

    cv = sub.add_parser("coverage", help="diagnostic des rapprochements de noms (pour data/aliases.json)")
    cv.add_argument("--limit", type=int, default=15)
    cv.add_argument("--offline", action="store_true")

    args = p.parse_args(argv)
    if args.cmd == "collect":
        log(f"Cache : {sources.cache_dir()}")
        status = sources.collect(force=args.force, only=_csv(args.only) or None, log=log)
        return 0 if not any(v.startswith("ÉCHEC") for v in status.values()) else 1

    if args.cmd == "coverage":
        models, history, lmarena, epoch, _perf = load_context(args.offline, log)
        index = ModelIndex(models["data"])
        signals = build_signals(index, lmarena, epoch, models.get("source_date"))
        usage = build_usage(index, history)
        print("## Modèles les plus utilisés et sources de qualité trouvées\n")
        for mid, u in list(usage["by_model"].items())[: args.limit]:
            found = [s for s, sig in signals.items() if mid in sig["scores"]]
            print(f"- #{u['rank']} {mid} : {', '.join(found) if found else 'AUCUNE'}")
        if usage["unresolved"]:
            print("\nSlugs d'usage sans modèle OpenRouter : " + ", ".join(k for k, _ in usage["unresolved"][: args.limit]))
        for sid, sig in signals.items():
            if sig.get("unmatched"):
                print(f"\n## {sid} : {sig['matched']}/{sig['entries']} rapprochés — meilleurs non rapprochés")
                print(", ".join(f"{n} ({pct:.0f})" for n, pct in sig["unmatched"][: args.limit]))
        return 0

    if args.cmd != "recommend":
        p.print_help()
        return 2
    models, history, lmarena, epoch, perf = load_context(args.offline, log)
    res = recommend(models, history, lmarena, epoch, perf_history=perf, query={
        "task": args.task, "sort": args.sort, "top": args.top, "authors": _csv(args.author),
        "min_context": args.min_context, "max_price": args.max_price, "min_quality": args.min_quality,
        "min_sources": args.min_sources, "open_weights": args.open_weights, "input_modalities": _csv(args.input),
        "tools": args.tools, "input_share": args.input_share, "price_weight": args.price_weight,
        "adoption_weight": args.adoption_weight, "include_free": args.include_free,
        "max_latency_ms": None if args.max_latency is None else int(args.max_latency * 1000),
        "min_throughput": args.min_speed,
    })
    print(json.dumps(res, ensure_ascii=False, indent=2) if args.format == "json" else to_markdown(res))
    return 0
