# syntax=docker/dockerfile:1
# Page web « Quel modèle IA choisir ? » (www.creapulse.fr/outils/quel-modele-ia)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1 \
    MODEL_PICKER_CACHE=/data

# curl : healthcheck Coolify (une image sans curl/wget fait échouer le healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-web.txt .
RUN pip install -r requirements-web.txt

COPY modelpicker ./modelpicker
COPY data ./data
COPY web ./web

# /data = volume persistant : instantanés quotidiens (dont l'historique d'usage sur 7 jours)
RUN useradd -r -u 10001 appuser && mkdir -p /data && chown -R appuser /app /data
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
