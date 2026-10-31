# Macro RAG backend (API/MCP) image — LIGHTWEIGHT. Build from the repo root:
#   docker build -f infra/docker/Dockerfile -t macrorag:latest .
#
# This image does NOT bundle the bge-m3 embedder / bge-reranker (torch). The
# heavy inference is hosted out-of-process by the sister project RagInference;
# the backend calls it over HTTP (RAGINFER_URL, default :8799) via the
# RemoteEmbedder / RemoteReranker clients. Deps therefore come from the trimmed
# requirements-serve.txt (no FlagEmbedding / sentence-transformers / playwright,
# hence no torch). Keep inference.fallback_local OFF (rag.prod.yaml) so a stopped
# service surfaces an error rather than trying to import the absent torch stack.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-serve.txt .
RUN pip install --no-cache-dir -r requirements-serve.txt

COPY src/ ./src/
COPY prompts/ ./prompts/
COPY configs/ ./configs/

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# Pipeline builds lazily on first request; embedding/rerank are delegated to the
# remote RagInference service, so startup is instant and holds no model weights.
CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000"]
