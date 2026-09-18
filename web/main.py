"""Page « Quel modèle IA choisir ? » — www.creapulse.fr/outils/quel-modele-ia.

Deux entrées : formulaire (déterministe, gratuit) et question libre (1 appel LLM plafonné).
Le classement vient toujours de modelpicker.scoring.recommend().
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from modelpicker.query import describe_query, sanitize_query, summary_sentence

from .ask import AskError, question_to_query
from .config import settings
from .data import store
from .limits import DailyBudget, QuestionCache, RateLimiter

log = logging.getLogger("model-picker")
logging.basicConfig(level=logging.DEBUG if settings.debug else logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# Le healthcheck Coolify interroge /health toutes les 5 s : sans ce filtre, il noie les traces de collecte.
logging.getLogger("uvicorn.access").addFilter(lambda r: "/health" not in r.getMessage())

BASE_DIR = Path(__file__).parent
STATIC_DIR = (BASE_DIR / "static").resolve()
STATIC_VERSION = hashlib.sha1(b"".join(p.read_bytes() for p in sorted(STATIC_DIR.glob("*")) if p.is_file())).hexdigest()[:10]
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

rate_limiter = RateLimiter(settings.ip_rate_limit_per_min)
budget = DailyBudget(settings.llm_global_daily_cap, settings.llm_ip_daily_cap)
ask_cache = QuestionCache(settings.ask_cache_size)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.reload()
    if not settings.llm_enabled:
        log.warning("LLM_API_KEY absente : question libre désactivée, formulaire seul")
    app.state.http = httpx.AsyncClient()
    task = asyncio.create_task(store.refresh_loop(settings.refresh_interval_s, settings.collect_on_startup))
    yield
    task.cancel()
    await app.state.http.aclose()


app = FastAPI(title="Quel modèle IA choisir ?", docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan, root_path=settings.root_path)


def client_ip(request: Request) -> str:
    if settings.trust_proxy_headers:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "code": code, "message": message}, status_code=status)


def _result(query: dict, **extra) -> JSONResponse:
    res = store.recommend(query)
    return JSONResponse({"ok": True, "summary": summary_sentence(res), "understood": describe_query(query),
                         "query": query, "result": res, **extra})


@app.get("/static/{filename}")
async def static_file(filename: str):
    target = (STATIC_DIR / filename).resolve()
    if target.parent != STATIC_DIR or not target.is_file():
        return _error(404, "not_found", "Fichier introuvable.")
    return FileResponse(str(target), headers={"Cache-Control": "public, max-age=3600"})


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    resp = templates.TemplateResponse(request, "index.html", {
        "root": settings.root_path,
        "static_version": STATIC_VERSION,
        "embed": request.query_params.get("embed") == "1",
        "llm_enabled": settings.llm_enabled,
        "question_max": settings.question_max_chars,
        "authors": sorted(store.authors),
    })
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/health")
async def health():
    from modelpicker import sources
    lmarena = (store.lmarena or {}).get("data") or {}
    try:
        import pyarrow  # noqa: F401
        parquet = True
    except Exception:
        parquet = False
    return {"status": "ok", "data_ready": store.ready,
            "prices_date": (store.models or {}).get("source_date"), "llm_enabled": settings.llm_enabled,
            "sources": {"lmarena_boards": sorted(lmarena), "lmarena_date": (store.lmarena or {}).get("source_date"),
                        "usage_days": len(store.history), "perf_days": len(store.perf), "pyarrow": parquet},
            "needs_refresh": sources.needs_refresh(), "cache": str(sources.cache_dir())}


@app.get("/api/insights")
async def api_insights():
    """Phrases datées des zones dynamiques de la landing (lues chaque jour par le workflow n8n)."""
    if not store.ready:
        return _error(503, "loading", "Données en cours de chargement.")
    from modelpicker.insights import build_insights
    with store._lock:
        res = build_insights(store.models, store.history, store.lmarena, store.epoch, store.perf)
    return JSONResponse({"ok": True, **res}, headers={"Cache-Control": "no-store"})


@app.get("/api/recommend")
async def api_recommend(request: Request):
    if not rate_limiter.allow(client_ip(request)):
        return _error(429, "rate_limited", "Trop de requêtes. Patientez une minute.")
    if not store.ready:
        return _error(503, "loading", "Les données du jour sont en cours de chargement. Réessayez dans une minute.")
    params = dict(request.query_params)
    params["authors"] = params.get("author") or params.get("authors")
    params["input_modalities"] = params.get("input") or params.get("input_modalities")
    return _result(sanitize_query(params, store.authors))


@app.post("/api/ask")
async def api_ask(request: Request, payload: dict = Body(...)):
    ip = client_ip(request)
    if not rate_limiter.allow(ip):
        return _error(429, "rate_limited", "Trop de requêtes. Patientez une minute.")
    if not store.ready:
        return _error(503, "loading", "Les données du jour sont en cours de chargement. Réessayez dans une minute.")
    question = " ".join(str(payload.get("question") or "").split())
    if len(question) < 5:
        return _error(400, "input", "Posez une question un peu plus précise.")
    if len(question) > settings.question_max_chars:
        return _error(400, "input", f"Question trop longue ({settings.question_max_chars} caractères maximum).")

    cached = ask_cache.get(question)
    if cached is not None:
        if cached.get("off_topic"):
            return _error(422, "off_topic", OFF_TOPIC)
        return _result(cached, cached_question=True)

    if not settings.llm_enabled:
        return _error(503, "llm_off", "La question libre est désactivée pour le moment : utilisez les filtres.")
    refusal = budget.reserve(ip)
    if refusal == "global_cap":
        return _error(429, "global_cap", "La question libre a atteint son quota du jour. Les filtres restent disponibles.")
    if refusal == "ip_cap":
        return _error(429, "ip_cap", "Vous avez atteint le nombre de questions libres du jour. Les filtres restent disponibles.")

    try:
        raw, usage = await question_to_query(question, store.authors, request.app.state.http)
    except AskError as exc:
        budget.release(ip)
        log.error("ask : %s", exc)
        return _error(503, "llm", "Je n'ai pas réussi à interpréter la question. Utilisez les filtres, ou reformulez.")
    log.info("ask ok tokens=%s/%s", usage.get("prompt_tokens"), usage.get("completion_tokens"))

    if raw.get("off_topic") is True:
        ask_cache.put(question, {"off_topic": True})
        return _error(422, "off_topic", OFF_TOPIC)
    query = sanitize_query(raw, store.authors)
    ask_cache.put(question, query)
    return _result(query)


OFF_TOPIC = "Cet outil répond aux questions sur le choix d'un modèle de langage (qualité, prix, usage). Reformulez dans ce sens."
