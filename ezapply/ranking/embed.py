"""Resume/JD similarity via fastembed (lazy-loaded)."""
from __future__ import annotations

MAX_CHARS = 2000


class Embedder:
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            import os

            # huggingface_hub has no default network timeout for its metadata check
            os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "15")
            os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def similarities(self, resume_text: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        import numpy as np

        model = self._load()
        vecs = np.array(list(model.embed([resume_text[:MAX_CHARS]] + [t[:MAX_CHARS] for t in texts])))
        vecs = vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-12)
        return [float(x) for x in vecs[1:] @ vecs[0]]
