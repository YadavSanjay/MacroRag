"""Production ASGI entrypoint.  Run with:

    PYTHONPATH=src uvicorn api.server:app --host 0.0.0.0 --port 8000

The pipeline (bge-m3 + Qdrant + bge-reranker + LLM) is built lazily on the first
request, so startup is instant and model loading is deferred.
"""

from __future__ import annotations

from pipelines.query_pipeline import build_pipeline
from utils.logging import setup_logging

from .app import create_app

setup_logging()
app = create_app(build_pipeline)
