from __future__ import annotations

from dataclasses import dataclass
from typing import List

# Optional dependency. If not installed, reranking is disabled.
try:
    from sentence_transformers import CrossEncoder  # type: ignore
except Exception:  # pragma: no cover
    CrossEncoder = None  # type: ignore


@dataclass
class Reranker:
    model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    max_pairs: int = 60

    def __post_init__(self) -> None:
        if CrossEncoder is None:
            self._model = None
        else:
            # local model download on first run (uses HF cache)
            self._model = CrossEncoder(self.model_name)

    def available(self) -> bool:
        return self._model is not None

    def rerank(self, query: str, passages: List[str]) -> List[float]:
        if self._model is None:
            # No rerank: all equal
            return [0.0 for _ in passages]
        pairs = [(query, p) for p in passages[: self.max_pairs]]
        scores = self._model.predict(pairs)  # type: ignore
        # Ensure list[float]
        return [float(s) for s in scores] + [0.0 for _ in passages[len(pairs) :]]
