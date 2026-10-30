"""Tokenization for mixed Chinese / English text.

v0 deliberately avoids a tokenizer dependency: Latin words are split on
non-word characters, CJK runs are indexed as character bigrams. This works
surprisingly well for short chat messages and costs nothing to run.

v1 will swap in a real tokenizer (jieba / bge-m3) behind the same interface.
"""

from __future__ import annotations

import re
from typing import Iterable, Set

_WORD = re.compile(r"[a-z0-9_]+")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str) -> Set[str]:
    """Return the token set for a piece of text."""
    text = (text or "").lower()
    out: Set[str] = set(_WORD.findall(text))

    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            out.add(run)
            continue
        for i in range(len(run) - 1):
            out.add(run[i : i + 2])

    return out


def jaccard(a: Set[str], b: Set[str]) -> float:
    """Similarity between two token sets, in [0, 1]."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / float(len(a | b))


def coverage(query: Set[str], doc: Set[str]) -> float:
    """Fraction of query tokens present in the document. Favours precision."""
    if not query:
        return 0.0
    return len(query & doc) / float(len(query))


def first_clause(text: str, max_chars: int = 24) -> str:
    """Cheap deterministic 'summary' used when no LLM is available."""
    text = (text or "").strip()
    for sep in ("，", ",", "。", ".", "！", "!", "？", "?", "；", ";"):
        idx = text.find(sep)
        if 0 < idx <= max_chars:
            return text[:idx]
    return text[:max_chars]


def dedupe(tokens: Iterable[str]) -> Set[str]:
    return {t for t in tokens if t}
