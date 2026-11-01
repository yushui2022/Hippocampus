"""Hippocampus -- a memory kernel that forgets like a human.

v2 keeps the graded-forgetting core and adds hybrid retrieval, episodes and
fine-grained facts:

    from hippocampus import Hippocampus, Event, HashingEmbedder

    hippo = Hippocampus(scope="user_42", embedder=HashingEmbedder())
    hippo.remember(Event(text="我数学考砸了，哭了好久"))
    hits = hippo.recall("最近有点学不动了")
    for hit in hits:
        print(hit.level, hit.text)
"""

from __future__ import annotations

from .episode import Episode, WindowSegmenter
from .facts import FactCandidate, NullFactExtractor, RuleFactExtractor
from .indexer import AsyncIndexer
from .kernel import Hippocampus
from .models import Engram, Event, Fact, RecallHit, Summary, TickReport
from .ops import backup_database, restore_database, snapshot
from .profiles import PRESETS, Profile, get_profile
from .retrieval import (
    BM25Index,
    Embedder,
    HashingEmbedder,
    LocalEmbedder,
    OpenAIEmbedder,
    VectorIndex,
    cosine,
)
from .store import Store

__version__ = "0.5.0"

__all__ = [
    "Hippocampus",
    "AsyncIndexer",
    "Event",
    "Engram",
    "Fact",
    "FactCandidate",
    "RecallHit",
    "Summary",
    "TickReport",
    "Episode",
    "WindowSegmenter",
    "Profile",
    "PRESETS",
    "get_profile",
    "Store",
    "BM25Index",
    "VectorIndex",
    "Embedder",
    "HashingEmbedder",
    "LocalEmbedder",
    "OpenAIEmbedder",
    "cosine",
    "RuleFactExtractor",
    "NullFactExtractor",
    "snapshot",
    "backup_database",
    "restore_database",
    "__version__",
]
