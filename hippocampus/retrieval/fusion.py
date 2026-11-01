"""Rank fusion and diversity."""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Sequence, Tuple


def rrf(rankings: Sequence[Sequence[str]], k: int = 60) -> Dict[str, float]:
    """Reciprocal Rank Fusion: robust when routes have incomparable scales."""
    fused: Dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank)
    return fused


def normalize(scores: Dict[str, float]) -> Dict[str, float]:
    if not scores:
        return {}
    values = list(scores.values())
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return {key: 1.0 for key in scores}
    return {key: (value - lo) / (hi - lo) for key, value in scores.items()}


def mmr(
    candidates: List[Tuple[str, float]],
    similarity: Callable[[str, str], float],
    k: int,
    lambda_: float = 0.7,
) -> List[str]:
    """Maximal Marginal Relevance, greedy O(k·n)."""
    if k <= 0:
        return []
    remaining = list(candidates)
    selected: List[str] = []
    while remaining and len(selected) < k:
        best_idx = 0
        best_value = float("-inf")
        for idx, (doc_id, score) in enumerate(remaining):
            if selected:
                redundancy = max(similarity(doc_id, other) for other in selected)
            else:
                redundancy = 0.0
            value = lambda_ * score - (1.0 - lambda_) * redundancy
            if value > best_value:
                best_value = value
                best_idx = idx
        selected.append(remaining.pop(best_idx)[0])
    return selected
