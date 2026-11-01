"""Vector index with a zero-dependency default.

Vectors are persisted in SQLite and cached in memory per scope. When NumPy is
available we use one matrix multiply; otherwise the loop still works, just
slower. FAISS can be added later behind the same interface.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set, Tuple

from ..store import Store
from .embeddings import cosine

try:  # optional accelerator
    import numpy as _np  # type: ignore
except Exception:  # pragma: no cover - environment dependent
    _np = None


class VectorIndex:
    def __init__(self, store: Store, model: str, dim: int) -> None:
        self.store = store
        self.model = model
        self.dim = dim
        self._cache: Dict[str, Dict[str, List[float]]] = {}
        self._matrix: Dict[str, Tuple[List[str], object]] = {}

    def fingerprint(self) -> str:
        return "{}:{}".format(self.model, self.dim)

    def _load(self, scope: str) -> Dict[str, List[float]]:
        if scope not in self._cache:
            self._cache[scope] = self.store.vectors_for_scope(scope)
            self._matrix.pop(scope, None)
        return self._cache[scope]

    def upsert(self, scope: str, engram_id: str, vec: Sequence[float]) -> None:
        self.store.upsert_vector(engram_id, self.model, self.dim, vec)
        if scope in self._cache:
            self._cache[scope][engram_id] = list(vec)
            self._matrix.pop(scope, None)

    def delete(self, engram_id: str) -> None:
        for scope, items in self._cache.items():
            if engram_id in items:
                items.pop(engram_id, None)
                self._matrix.pop(scope, None)
        self.store.delete_vector(engram_id)

    def search(
        self,
        scope: str,
        query: Sequence[float],
        k: int = 50,
        allowed: Optional[Set[str]] = None,
    ) -> List[Tuple[str, float]]:
        items = self._load(scope)
        if not items:
            return []
        if _np is not None and len(items) >= 64:
            ids, matrix = self._matrix_for(scope, items)
            q = _np.array(list(query), dtype="float64")
            if matrix.shape[1] != q.shape[0]:
                return []
            q = _np.nan_to_num(q, nan=0.0, posinf=0.0, neginf=0.0)
            with _np.errstate(all="ignore"):
                q_norm = float(_np.linalg.norm(q))
                if not _np.isfinite(q_norm) or q_norm <= 1e-12:
                    return []
                scores = matrix @ (q / q_norm)
            scores = _np.nan_to_num(
                scores, nan=-1.0, posinf=0.0, neginf=0.0
            )
            pairs = list(zip(ids, scores.tolist()))
        else:
            pairs = [(i, cosine(query, v)) for i, v in items.items()]

        if allowed is not None:
            pairs = [(i, s) for i, s in pairs if i in allowed]
        pairs.sort(key=lambda item: item[1], reverse=True)
        return pairs[:k]

    def invalidate(self, scope: Optional[str] = None) -> None:
        if scope is None:
            self._cache.clear()
            self._matrix.clear()
        else:
            self._cache.pop(scope, None)
            self._matrix.pop(scope, None)

    def _matrix_for(self, scope: str, items: Dict[str, List[float]]):
        cached = self._matrix.get(scope)
        if cached is not None:
            return cached
        ids = list(items.keys())
        matrix = _np.nan_to_num(
            _np.array([items[i] for i in ids], dtype="float64"),
            nan=0.0,
            posinf=0.0,
            neginf=0.0,
        )
        with _np.errstate(all="ignore"):
            norms = _np.linalg.norm(matrix, axis=1, keepdims=True)
            matrix = matrix / _np.maximum(norms, 1e-12)
        self._matrix[scope] = (ids, matrix)
        return ids, matrix

    def remove_scope(self, scope: str) -> None:
        for engram_id in list(self._cache.get(scope, {})):
            self.store.delete_vector(engram_id)
        self._cache.pop(scope, None)
        self._matrix.pop(scope, None)
