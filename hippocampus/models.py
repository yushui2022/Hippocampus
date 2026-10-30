"""Core data types.

The kernel only knows about `Event` and `Engram`. It has no idea what an
"idol" or a "danmaku" is -- that belongs to the profile / adapter layer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str = "eng") -> str:
    return "{}_{}".format(prefix, uuid.uuid4().hex[:12])


@dataclass
class Event:
    """Something that happened, from any channel (IM, danmaku, ticket...)."""

    text: str
    actor: str = "user"
    at: Optional[datetime] = None
    scene: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def resolved_at(self) -> datetime:
        return self.at or utcnow()


@dataclass
class Engram:
    """One memory trace.

    `level` is how degraded the trace is:
        0 = verbatim, 1 = summary, 2 = gist, 3 = emotional residue, 4 = archived
    """

    engram_id: str = field(default_factory=new_id)
    scope: str = "default"
    kind: str = "episodic"  # episodic | semantic
    raw_text: str = ""
    gist: str = ""
    emotion: str = ""
    level: int = 0
    importance: float = 0.5
    stability: float = 1.0
    difficulty: float = 5.0
    active_age_days: float = 0.0
    recall_count: int = 0
    created_at: datetime = field(default_factory=utcnow)
    last_recall_at: Optional[datetime] = None
    last_mention_at: Optional[datetime] = None
    refs: List[str] = field(default_factory=list)
    tokens: Set[str] = field(default_factory=set)
    meta: Dict[str, Any] = field(default_factory=dict)

    # -- rendering -----------------------------------------------------------
    def render(self, now: Optional[datetime] = None) -> str:
        """How the memory surfaces in a prompt.

        This is the single most important method in the library: the model
        must not speak verbatim text for a half-forgotten memory.
        """
        if self.level <= 0:
            return self.raw_text
        if self.level == 1:
            return self.gist or self.raw_text
        if self.level == 2:
            return self.gist or _short(self.raw_text)
        if self.level == 3:
            when = _vague_period(self.created_at, now or utcnow())
            mood = self.emotion or "你状态不太一样"
            return "隐约记得{}那阵子{}，{}".format(
                when, mood, _l3_tail(self.engram_id)
            )
        return ""

    def is_archived(self, max_level: int) -> bool:
        return self.level >= max_level

    def age_days(self, now: datetime) -> float:
        return max(0.0, (now - self.created_at).total_seconds() / 86400.0)


def _short(text: str, n: int = 20) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


_L3_TAILS = (
    "具体细节想不起来了。",
    "细节有点模糊。",
    "只记得个大概。",
    "具体说了什么记不清了。",
)


def _l3_tail(seed: str) -> str:
    """Humans don't phrase a half-memory identically every time."""
    return _L3_TAILS[sum(ord(c) for c in seed) % len(_L3_TAILS)]


def _vague_period(when: datetime, now: datetime) -> str:
    days = max(0.0, (now - when).total_seconds() / 86400.0)
    if days < 2:
        return "前两天"
    if days < 10:
        return "上周"
    if days < 40:
        return "上个月"
    if days < 400:
        return "几个月前"
    return "很久以前"


@dataclass
class RecallHit:
    """What `recall()` hands back to the caller."""

    engram: Engram
    score: float
    text: str

    @property
    def level(self) -> int:
        return self.engram.level

    @property
    def refs(self) -> List[str]:
        return self.engram.refs

    def __repr__(self) -> str:
        return "RecallHit(score={:.3f}, level={}, text={!r})".format(
            self.score, self.level, self.text
        )
