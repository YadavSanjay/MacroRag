"""BAAI/bge-m3 embedder (the ARag backbone): one forward pass yields a 1024-d
dense vector and sparse lexical weights, both used by Qdrant hybrid search."""

from __future__ import annotations

from utils.config import EmbeddingSettings
from utils.logging import get_logger
from utils.schema import Embedding, SparseVector

from .base import Embedder

log = get_logger(__name__)


class BGEM3Embedder(Embedder):
    def __init__(self, settings: EmbeddingSettings | None = None):
        self.settings = settings or EmbeddingSettings()
        self.dense_dim = self.settings.dense_dim
        self._model = None  # lazy: avoid importing torch until first use

    def _ensure(self):
        if self._model is None:
            from FlagEmbedding import BGEM3FlagModel  # heavy import, deferred
            log.info("loading embedder %s", self.settings.model)
            self._model = BGEM3FlagModel(self.settings.model, use_fp16=True,
                                         device=self.settings.device)
        return self._model

    def _encode(self, texts: list[str]) -> list[Embedding]:
        if not texts:
            return []
        model = self._ensure()
        out = model.encode(texts, batch_size=self.settings.batch_size,
                           max_length=self.settings.max_length,
                           return_dense=True, return_sparse=True,
                           return_colbert_vecs=False)
        dense = out["dense_vecs"]
        lexical = out["lexical_weights"]
        embeddings: list[Embedding] = []
        for i in range(len(texts)):
            weights = lexical[i]  # {token_id(str): weight}
            sv = SparseVector(indices=[int(k) for k in weights],
                              values=[float(v) for v in weights.values()])
            embeddings.append(Embedding(dense=[float(x) for x in dense[i]], sparse=sv))
        return embeddings

    def embed_documents(self, texts: list[str]) -> list[Embedding]:
        return self._encode(texts)

    def embed_queries(self, texts: list[str]) -> list[Embedding]:
        return self._encode(texts)
