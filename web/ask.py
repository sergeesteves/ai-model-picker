"""Question libre -> requête JSON, via un seul appel à un petit LLM (OmniRoute).

Le LLM ne classe rien et ne rédige rien : il remplit un formulaire. Sa sortie passe ensuite par
modelpicker.query.sanitize_query (liste blanche), donc une réponse farfelue ne peut rien casser.
"""
from __future__ import annotations

import json
import re

import httpx

from .config import settings

SYSTEM_PROMPT = """Tu convertis une question sur le choix d'un modèle de langage (LLM) en filtres JSON pour un comparateur.
Réponds UNIQUEMENT par un objet JSON, sans texte autour.

Champs (omets ceux que la question ne mentionne pas) :
- "task" : "code" (programmation, dev, web, refactor), "agentic" (agents, outils, automatisation, tool calling), "redaction" (rédiger : article, blog, SEO, GEO, newsletter, page web, post social, copywriting), "redaction_fr" (rédiger en français, ou question posée pour du contenu francophone), "documents" (résumer ou analyser des documents longs, PDF, transcriptions, audits), "general" (chat, questions-réponses, traduction, tout le reste).
- "sort" : "value" (rapport qualité/prix ; À UTILISER PAR DÉFAUT dès que la question ne demande pas explicitement autre chose), "quality" (seulement si la question demande le meilleur ou le plus performant, budget indifférent), "price" (le moins cher, le plus économique), "usage" (le plus utilisé, le plus populaire), "fast" (rapide, réactif, faible latence, temps réel, chat en direct, streaming). « Fiable » ne change pas le tri.
- "authors" : liste d'éditeurs parmi : {authors}. Correspondances : OpenAI/GPT -> openai ; Claude -> anthropic ; Gemini/Gemma -> google ; Mistral -> mistralai ; Llama/Meta -> meta ; Grok/xAI -> x-ai ; Qwen/Alibaba -> qwen ; Kimi/Moonshot -> moonshotai ; GLM/Zhipu -> z-ai.
- "min_context" : contexte minimum en tokens (entier ; « un million » -> 1000000, « 200k » -> 200000).
- "max_price" : prix maximum en dollars par million de tokens (nombre).
- "min_quality" : qualité minimum sur 100 ; « correct/décent/acceptable » -> 50, « bon » -> 60, « très bon/excellent » -> 70. Si sort vaut "price" et que la question ne précise rien, mets 50.
- "min_sources" : 2 si la question demande une valeur sûre, fiable, éprouvée.
- "open_weights" : true si open source, poids ouverts, auto-hébergeable, local.
- "tools" : true si appel d'outils / function calling est requis.
- "input_modalities" : parmi ["image", "audio", "video"], seulement si le modèle doit analyser des images, de l'audio ou de la vidéo. Les PDF et documents ne demandent AUCUNE modalité (le texte en est extrait).
- "max_latency_ms" : latence maximum avant le premier token, en millisecondes (« moins d'une seconde » -> 1000).
- "min_throughput" : vitesse minimum en tokens par seconde (« au moins 50 tokens/s » -> 50 ; « très rapide » sans chiffre -> 80).
- "top" : nombre de modèles demandés (1 à 10).
- "off_topic" : true si la question ne porte pas sur le choix d'un modèle de langage (et rien d'autre).

Exemple : « le moins cher chez Google avec 1M de contexte et un niveau agentique correct »
-> {{"task": "agentic", "sort": "price", "authors": ["google"], "min_context": 1000000, "min_quality": 50}}
Exemple : « un modèle open source fiable pour résumer des PDF »
-> {{"task": "general", "sort": "value", "open_weights": true, "min_sources": 2}}
Exemple : « un modèle rapide et bon pour un chatbot, réponse en moins d'une seconde »
-> {{"task": "general", "sort": "fast", "min_quality": 60, "max_latency_ms": 1000}}"""


class AskError(RuntimeError):
    pass


def build_messages(question: str, authors: set[str]) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(authors=", ".join(sorted(authors)))},
        {"role": "user", "content": question},
    ]


def parse_json_object(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise AskError("aucun objet JSON dans la réponse")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise AskError(f"JSON invalide : {exc}") from exc
    if not isinstance(obj, dict):
        raise AskError("la réponse n'est pas un objet")
    return obj


async def question_to_query(question: str, authors: set[str], client: httpx.AsyncClient) -> tuple[dict, dict]:
    """Renvoie (objet brut du LLM, usage de tokens)."""
    payload = {
        "model": settings.llm_model,
        "messages": build_messages(question, authors),
        "temperature": 0,
        "max_tokens": 250,
    }
    try:
        resp = await client.post(f"{settings.llm_base_url}/chat/completions", json=payload,
                                 headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                                 timeout=settings.llm_timeout_s)
    except httpx.HTTPError as exc:
        raise AskError(f"LLM injoignable : {exc}") from exc
    if resp.status_code != 200:
        raise AskError(f"LLM HTTP {resp.status_code} : {resp.text[:200]}")
    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AskError("réponse LLM inattendue") from exc
    return parse_json_object(content), data.get("usage") or {}
