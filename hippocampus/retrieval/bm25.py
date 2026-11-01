"""BM25 over the persisted token index.

The token table already stores CJK bigrams and Latin words, so BM25 needs no
tokenizer and no FTS5 extension. The index is cached per scope and rebuilt
whenever ``Store.version`` changes.
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence, Set, Tuple

from ..store import Store
from ..tokens import bm25_score


class BM25Index:
    def __init__(self, store: Store, k1: float = 1.2, b: float = 0.75) -> None:
        self.store = store
        self.k1 = k1
        self.b = b
        self._cache: Dict[Tuple[str, int], Tuple[int, List[Tuple[str, int, Set[str]]], Dict[str, int], float]] = {}

    def _build(self, scope: str, max_level: int):
        docs = self.store.token_docs(scope, max_level)
        lengths = [len(tokens) for _, tokens in docs]
        avg_len = (sum(lengths) / float(len(lengths))) if lengths else 1.0
        df: Dict[str, int] = {}
        for _, tokens in docs:
            for token in tokens:
                df[token] = df.get(token, 0) + 1
        rows = [(doc_id, len(tokens), tokens) for doc_id, tokens in docs]
        return self.store.version, rows, df, avg_len

    def _ensure(self, scope: str, max_level: int):
        key = (scope, max_level)
        cached = self._cache.get(key)
        if cached is not None and cached[0] == self.store.version:
            return cached
        built = self._build(scope, max_level)
        self._cache[key] = built
        return built

    def search(
        self, scope: str, query: Set[str], max_level: int, limit: int = 50
    ) -> List[Tuple[str, float]]:
        if not query:
            return []
        _version, rows, df, avg_len = self._ensure(scope, max_level)
        n_docs = len(rows)
        if not n_docs:
            return []
        scored: List[Tuple[str, float]] = []
        for doc_id, doc_len, tokens in rows:
            score = bm25_score(
                query,
                tokens,
                doc_len=doc_len,
                avg_len=avg_len,
                df_map=df,
                n_docs=n_docs,
                k1=self.k1,
                b=self.b,
            )
            if score > 0:
                scored.append((doc_id, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def invalidate(self) -> None:
        self._cache.clear()
