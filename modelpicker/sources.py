"""Collecte des sources et cache local quotidien (stdlib uniquement).

Chaque source est enregistrée dans <cache>/snapshots/AAAA-MM-JJ/<source>.json sous la forme
{"source", "fetched_at", "source_date", "data"}. Une source en échec n'empêche pas les autres :
la commande recommend retombe alors sur le dernier instantané disponible.
"""
from __future__ import annotations

import concurrent.futures
import csv
import datetime as dt
import io
import json
import logging
import os
import socket
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

USER_AGENT = "ai-model-picker/0.1"
log = logging.getLogger("modelpicker.sources")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
# Endpoint non documenté (celui de la page Rankings) : seule la dernière date est complète.
OPENROUTER_RANKINGS_URL = "https://openrouter.ai/api/frontend/v1/rankings/models?view=day"
# Endpoint non documenté de l'onglet Performance d'une page modèle : stats par hébergeur (30 dernières minutes)
OPENROUTER_PERF_URL = "https://openrouter.ai/api/frontend/v1/stats/endpoint"
HF_ROWS_URL = "https://datasets-server.huggingface.co/rows"
HF_TREE_URL = "https://huggingface.co/api/datasets/lmarena-ai/leaderboard-dataset/tree/main"
HF_RESOLVE_URL = "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset/resolve/main"
LMARENA_DATASET = "lmarena-ai/leaderboard-dataset"
# (config du dataset, catégorie) -> identifiant de tableau
LMARENA_BOARDS = {
    "lmarena_text": ("text", "overall"),
    "lmarena_coding": ("text", "coding"),
    "lmarena_creative_writing": ("text", "creative_writing"),
    "lmarena_instruction_following": ("text", "instruction_following"),
    "lmarena_french": ("text", "french"),
    "lmarena_longer_query": ("text", "longer_query"),
    "lmarena_webdev": ("webdev", "overall"),
    "lmarena_agent": ("agent", "overall"),
}
EPOCH_ZIP_URL = "https://epoch.ai/data/benchmark_data.zip"
# Au-delà, on considère la voie parquet comme indisponible (pyarrow absent, lent à charger, réseau bloqué).
HF_PARQUET_DEADLINE_S = _int_env("HF_PARQUET_DEADLINE_S", 45)

SOURCES = ("openrouter_models", "openrouter_usage", "openrouter_perf", "lmarena", "epoch")


def _force_ipv4_if_asked() -> None:
    """Certains conteneurs annoncent l'IPv6 sans route utilisable : la connexion reste pendue au lieu
    d'échouer, et le timeout de socket ne couvre pas la résolution. FORCE_IPV4=1 ne garde que l'IPv4."""
    if os.getenv("FORCE_IPV4", "").strip().lower() not in ("1", "true", "yes", "on"):
        return
    original = socket.getaddrinfo

    def ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
        infos = original(host, port, socket.AF_INET, type, proto, flags)
        return infos or original(host, port, family, type, proto, flags)

    socket.getaddrinfo = ipv4_only
    log.info("réseau : résolution limitée à l'IPv4 (FORCE_IPV4)")


_force_ipv4_if_asked()


def cache_dir() -> Path:
    base = os.environ.get("MODEL_PICKER_CACHE")
    return Path(base) if base else Path.home() / ".cache" / "ai-model-picker"


def _http_get(url: str, timeout: int = 90, retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:  # réseau, 5xx, timeout
            last = exc
            log.warning("GET %s : tentative %d en échec (%s)", url.split("?")[0], attempt + 1, exc)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} : {last}")


def _get_json(url: str):
    return json.loads(_http_get(url).decode("utf-8"))


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- OpenRouter : modèles
def fetch_openrouter_models() -> dict:
    raw = _get_json(OPENROUTER_MODELS_URL)["data"]
    data = []
    for m in raw:
        pricing = m.get("pricing") or {}
        arch = m.get("architecture") or {}
        bench = m.get("benchmarks") or {}
        data.append({
            "id": m.get("id"),
            "canonical_slug": m.get("canonical_slug"),
            "name": m.get("name"),
            "created": m.get("created"),
            "context_length": m.get("context_length"),
            "price_in": _to_float(pricing.get("prompt")),
            "price_out": _to_float(pricing.get("completion")),
            "input_modalities": arch.get("input_modalities") or [],
            "output_modalities": arch.get("output_modalities") or [],
            "supported_parameters": m.get("supported_parameters") or [],
            "hugging_face_id": m.get("hugging_face_id") or None,
            "expiration_date": m.get("expiration_date"),
            "artificial_analysis": bench.get("artificial_analysis") or None,
        })
    return {"source_date": dt.date.today().isoformat(), "data": data}


def _to_float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- OpenRouter : usage
def fetch_openrouter_usage() -> dict:
    rows = _get_json(OPENROUTER_RANKINGS_URL)["data"]
    today = dt.date.today().isoformat()
    dates = sorted({(r.get("date") or "")[:10] for r in rows if r.get("date")})
    complete = [d for d in dates if d < today]  # le jour courant est partiel : toujours J-1 au plus tard
    if not complete:
        raise RuntimeError(f"rankings : aucune journée complète (dates reçues : {dates})")
    day = complete[-1]
    by_slug: dict[str, dict] = {}
    for r in rows:
        if (r.get("date") or "")[:10] != day:
            continue
        slug = r.get("model_permaslug")
        if not slug or "/" not in slug:
            continue
        a = by_slug.setdefault(slug, {"prompt": 0, "completion": 0, "requests": 0, "tool_calls": 0})
        a["prompt"] += r.get("total_prompt_tokens") or 0
        a["completion"] += r.get("total_completion_tokens") or 0
        a["requests"] += r.get("count") or 0
        a["tool_calls"] += r.get("total_tool_calls") or 0
    return {"source_date": day, "data": by_slug}


# ---------------------------------------------------------------- OpenRouter : vitesse et latence
def _perf_candidates(models: list[dict]) -> list[dict]:
    from .match import is_listed_model  # import local : match n'importe pas sources

    return [m for m in models if is_listed_model(m) and m.get("canonical_slug")
            and m.get("price_in") is not None and m["price_in"] >= 0 and m.get("price_out") is not None
            and not (m["price_in"] == 0 and m["price_out"] == 0) and "text" in (m.get("output_modalities") or [])]


def fetch_openrouter_perf() -> dict:
    """Latence médiane avant le premier token et débit médian par hébergeur, fenêtre des 30 dernières minutes.

    Endpoint non documenté de la page modèle d'OpenRouter (onglet Performance) : un appel par modèle,
    espacés pour rester raisonnable (~323 appels, ~3 min).
    """
    snap = load_latest("openrouter_models")
    if not snap:
        raise RuntimeError("openrouter_perf : collecter openrouter_models d'abord")
    candidates = _perf_candidates(snap["data"])
    data, failures = {}, 0
    for m in candidates:
        qs = urllib.parse.urlencode({"latencyMetric": "latency", "perfWorkload": "text_generation",
                                     "permaslug": m["canonical_slug"], "variant": "standard"})
        try:
            rows = json.loads(_http_get(f"{OPENROUTER_PERF_URL}?{qs}", timeout=30, retries=2).decode("utf-8"))["data"]
        except Exception:
            failures += 1
            continue
        endpoints = []
        for e in rows or []:
            st = e.get("stats") or {}
            if st.get("p50_latency") is None and st.get("p50_throughput") is None:
                continue
            endpoints.append({"provider": e.get("provider_name"), "status": e.get("status"),
                              "latency_ms": st.get("p50_latency"), "throughput_tps": st.get("p50_throughput"),
                              "requests": st.get("request_count") or 0})
        if endpoints:
            data[m["id"]] = endpoints
        time.sleep(0.3)
    if candidates and failures > len(candidates) / 2:
        raise RuntimeError(f"openrouter_perf : {failures}/{len(candidates)} appels en échec")
    return {"source_date": dt.date.today().isoformat(), "measured_at": _now_iso(), "data": data}


# ---------------------------------------------------------------- LMArena (Hugging Face)
def _hf_parquet_rows(config: str) -> list[dict]:
    """Voie rapide : fichiers parquet du split `latest` (1 requête par fichier), si pyarrow est installé."""
    import pyarrow.parquet as pq  # dépendance optionnelle

    log.info("lmarena : lecture de l'index parquet de %s", config)
    tree = json.loads(_http_get(f"{HF_TREE_URL}/{config}", timeout=30, retries=2).decode("utf-8"))
    files = sorted(f["path"] for f in tree if f.get("path", "").startswith(f"{config}/latest-") and f["path"].endswith(".parquet"))
    if not files:
        raise RuntimeError(f"LMArena : aucun fichier parquet latest pour {config}")
    rows = []
    for path in files:
        started = time.monotonic()
        log.info("lmarena : téléchargement de %s", path)
        blob = _http_get(f"{HF_RESOLVE_URL}/{path}", timeout=45, retries=2)
        rows.extend(pq.read_table(io.BytesIO(blob)).to_pylist())
        log.info("lmarena : %s lu en parquet (%d Ko, %.1f s)", path, len(blob) // 1024, time.monotonic() - started)
    return rows


def _hf_api_rows(config: str) -> list[dict]:
    """Repli sans dépendance : API /rows du dataset viewer, pages de 100, rythme lent (429 sinon)."""
    out, offset, failures = [], 0, 0
    log.info("lmarena : lecture de %s par l'API /rows", config)
    while True:
        qs = urllib.parse.urlencode({"dataset": LMARENA_DATASET, "config": config, "split": "latest",
                                     "offset": offset, "length": 100})
        try:
            page = json.loads(_http_get(f"{HF_ROWS_URL}?{qs}", timeout=60, retries=1).decode("utf-8"))
        except RuntimeError:
            failures += 1
            if failures > 6:
                raise
            time.sleep(10 * failures)
            continue
        rows = [r["row"] for r in page.get("rows", [])]
        out.extend(rows)
        offset += len(rows)
        if not rows or offset >= page.get("num_rows_total", 0):
            log.info("lmarena : %s = %d lignes via l'API /rows", config, len(out))
            return out
        time.sleep(0.6)


def _hf_rows(config: str) -> list[dict]:
    """Parquet si possible ; sinon l'API du dataset viewer, plus lente mais sans dépendance.

    La voie parquet est bornée dans le temps : en prod, l'import de pyarrow s'est déjà bloqué sans jamais
    rendre la main (aucune trace, collecte figée). Passé le délai, on bascule sur l'API plutôt que d'attendre.
    """
    log.info("lmarena : collecte de %s", config)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(_hf_parquet_rows, config).result(timeout=HF_PARQUET_DEADLINE_S)
    except concurrent.futures.TimeoutError:
        log.warning("lmarena : voie parquet trop lente pour %s (> %d s), repli sur l'API /rows",
                    config, HF_PARQUET_DEADLINE_S)
    except ImportError:
        log.info("lmarena : pyarrow absent, repli sur l'API /rows pour %s", config)
    except Exception as exc:  # réseau, 5xx, parquet illisible : le repli reste possible
        log.warning("lmarena : parquet indisponible pour %s (%s), repli sur l'API /rows", config, exc)
    finally:
        # wait=False : un thread bloqué (import pyarrow figé, vu en prod) ne doit pas retenir la collecte.
        pool.shutdown(wait=False, cancel_futures=True)
    return _hf_api_rows(config)


def _arena_score(r: dict):
    # Elo (« rating ») pour text/webdev ; « score » (sans unité, plus haut = mieux) pour agent
    return r.get("rating") if r.get("rating") is not None else r.get("score")


def fetch_lmarena() -> dict:
    by_config = {}
    for config in sorted({c for c, _ in LMARENA_BOARDS.values()}):
        started = time.monotonic()
        by_config[config] = _hf_rows(config)
        log.info("lmarena : %s = %d lignes (%.1f s)", config, len(by_config[config]), time.monotonic() - started)
    boards = {}
    for board_id, (config, category) in LMARENA_BOARDS.items():
        rows = [r for r in by_config[config] if r.get("category") == category]
        last = max((r.get("leaderboard_publish_date") or "" for r in rows), default="")
        boards[board_id] = {
            "date": last,
            "entries": [
                {"name": r["model_name"], "organization": r.get("organization"), "license": r.get("license"),
                 "score": _arena_score(r), "votes": r.get("vote_count") or r.get("session_count")}
                for r in rows if (r.get("leaderboard_publish_date") or "") == last and _arena_score(r) is not None
            ],
        }
    dates = [b["date"] for b in boards.values() if b["date"]]
    return {"source_date": max(dates) if dates else None, "data": boards}


# ---------------------------------------------------------------- Epoch AI (Capabilities Index)
def fetch_epoch() -> dict:
    z = zipfile.ZipFile(io.BytesIO(_http_get(EPOCH_ZIP_URL, timeout=180)))
    with z.open("epoch_capabilities_index/eci_scores.csv") as fh:
        rows = list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8")))
    entries = []
    for r in rows:
        score = _to_float(r.get("eci"))
        if score is None:
            continue
        entries.append({"name": r.get("Display name") or r.get("Model"), "alt_name": r.get("Model"),
                        "organization": r.get("Organization"), "accessibility": r.get("Accessibility group"),
                        "date": r.get("date"), "score": score})
    last = max((e["date"] or "" for e in entries), default=None)
    return {"source_date": last, "data": {"epoch_eci": {"date": last, "entries": entries}}}


FETCHERS = {
    "openrouter_models": fetch_openrouter_models,
    "openrouter_usage": fetch_openrouter_usage,
    "openrouter_perf": fetch_openrouter_perf,
    "lmarena": fetch_lmarena,
    "epoch": fetch_epoch,
}


# ---------------------------------------------------------------- cache
def _snap_dir(day: str) -> Path:
    return cache_dir() / "snapshots" / day


def _cached_and_complete(name: str, path: Path) -> bool:
    """Un instantané du jour ne dispense d'une collecte que s'il contient tout ce que le code attend."""
    try:
        return is_complete(name, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return False


def collect(force: bool = False, only: list[str] | None = None, log=print) -> dict:
    """Collecte les sources absentes du jour (ou toutes si force). Renvoie {source: statut}."""
    day = dt.date.today().isoformat()
    status = {}
    for name in only or SOURCES:
        path = _snap_dir(day) / f"{name}.json"
        if path.exists() and not force and _cached_and_complete(name, path):
            status[name] = "déjà en cache"
            log(f"  {name} : {status[name]}")
            continue
        try:
            snap = FETCHERS[name]()
            snap = {"source": name, "fetched_at": _now_iso(), **snap}
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
            status[name] = f"OK (données du {snap.get('source_date')})"
        except Exception as exc:
            status[name] = f"ÉCHEC : {exc}"
        log(f"  {name} : {status[name]}")
    return status


def _snapshot_days() -> list[str]:
    root = cache_dir() / "snapshots"
    return sorted((p.name for p in root.iterdir() if p.is_dir()), reverse=True) if root.exists() else []


def load_latest(name: str) -> dict | None:
    for day in _snapshot_days():
        path = _snap_dir(day) / f"{name}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def load_history(name: str, days: int = 7) -> list[dict]:
    """Instantanés distincts par date de données (le plus récent d'abord), au plus `days`."""
    seen, out = set(), []
    for day in _snapshot_days():
        path = _snap_dir(day) / f"{name}.json"
        if not path.exists():
            continue
        snap = json.loads(path.read_text(encoding="utf-8"))
        if snap["source_date"] in seen:
            continue
        seen.add(snap["source_date"])
        out.append(snap)
        if len(out) >= days:
            break
    return out


def load_usage_history(days: int = 7) -> list[dict]:
    return load_history("openrouter_usage", days)


def is_complete(name: str, snap: dict | None) -> bool:
    """Un instantané du jour peut être périmé par le code : une source à laquelle on vient d'ajouter un
    tableau (ex. une catégorie LMArena) doit être recollectée, sinon la nouveauté n'arrive que le lendemain."""
    if not snap or not snap.get("data"):
        return False
    if name == "lmarena":
        return set(LMARENA_BOARDS).issubset(snap["data"])
    return True


def needs_refresh() -> list[str]:
    day = dt.date.today().isoformat()
    missing = []
    for name in SOURCES:
        path = _snap_dir(day) / f"{name}.json"
        if not path.exists():
            missing.append(name)
            continue
        try:
            if not is_complete(name, json.loads(path.read_text(encoding="utf-8"))):
                missing.append(name)
        except (OSError, json.JSONDecodeError):
            missing.append(name)
    return missing
