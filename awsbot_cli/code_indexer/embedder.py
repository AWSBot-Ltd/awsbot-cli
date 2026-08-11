from __future__ import annotations

from dataclasses import dataclass
from typing import List
import numpy as np

try:
    from fastembed import TextEmbedding
except Exception:  # pragma: no cover
    TextEmbedding = None  # type: ignore


class EmbedderError(RuntimeError):
    pass


@dataclass
class Embedder:
    model_name: str = "BAAI/bge-small-en-v1.5"

    def __post_init__(self) -> None:
        if TextEmbedding is None:
            raise EmbedderError(
                "fastembed is not installed. Install with: pip install fastembed (or fastembed-gpu)"
            )
        # FastEmbed supports query/passsage prefixes for retrieval models.
        self._model = TextEmbedding(model_name=self.model_name)

    def dim(self) -> int:
        # FastEmbed returns numpy arrays, get dim from a single embed.
        vec = next(iter(self._model.embed(["query: dimension probe"])))
        return int(vec.shape[0])

    def embed_query(self, text: str) -> List[float]:
        v = next(iter(self._model.embed([f"query: {text}"])))
        return v.astype(np.float32).tolist()

    def embed_passages(self, passages: list[str]) -> list[list[float]]:
        # Generator -> list
        vecs = list(self._model.embed([f"passage: {p}" for p in passages]))
        return [v.astype(np.float32).tolist() for v in vecs]
