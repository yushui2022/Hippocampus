"""Core data types.

The kernel only knows about `Event`, `Engram`, `Fact` and `RecallHit`.
Anything product-specific belongs to the profile / adapter layer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from .tokens import first_clause


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str = "eng") -> str:
    return "{}_{}".format(prefix, uuid.uuid4().hex[:12])


@dataclass
class Event:
    """Something that happened, from any channel (IM, ticket, batch...)."""

    text: str
    actor: str = "user"
    at: Optional[datetime] = None
    scene: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def resolved_at(self) -> datetime:
        return self.at or utcnow()


@dataclass
class Utterance:
    """One perceived input, with its situation and its source.

    The kernel stores this in the hippocampal buffer first. Nothing here calls
    an LLM: ``Brain.observe`` must stay O(1) writes (invariant 12).
    """

    text: str
    context_id: str = "default"
    context_kind: str = "dyad"  # dyad | group
    context_size: int = 2
    speaker_id: str = "user"
    speaker_name: str = ""
    actor: str = "user"  # user | assistant
    addressed: bool = False
    witnesses: List[str] = field(default_factory=list)
    visibility: str = ""  # private | shared | public; inferred when empty
    at: Optional[datetime] = None
    refs: List[str] = field(default_factory=list)
    kind: str = "speech"  # speech | action | expression | scene
    meta: Dict[str, Any] = field(default_factory=dict)

    def resolved_at(self) -> datetime:
        return self.at or utcnow()

    def resolved_visibility(self) -> str:
        if self.visibility in ("private", "shared", "public"):
            return self.visibility
        if self.context_kind == "group":
            return "shared"
        return "private"

    def source(self) -> Dict[str, Any]:
        return {
            "context_id": self.context_id,
            "context_kind": self.context_kind,
            "speakers": [self.speaker_id],
            "witnesses": list(dict.fromkeys(self.witnesses or [self.speaker_id])),
            "visibility": self.resolved_visibility(),
            "at": self.resolved_at().isoformat(),
        }


@dataclass
class StagingItem:
    """One buffered perception in the hippocampal staging table."""

    staging_id: str = field(default_factory=lambda: new_id("stg"))
    scope: str = "default"
    context_id: str = "default"
    context_kind: str = "dyad"
    context_size: int = 2
    speaker_id: str = "user"
    speaker_name: str = ""
    actor: str = "user"
    text: str = ""
    addressed: bool = False
    witnesses: List[str] = field(default_factory=list)
    visibility: str = "private"
    refs: List[str] = field(default_factory=list)
    tokens: Set[str] = field(default_factory=set)
    at: datetime = field(default_factory=utcnow)
    status: str = "pending"  # pending | promoted | dropped
    engram_id: Optional[str] = None
    salience: Optional[float] = None

    def source(self) -> Dict[str, Any]:
        return {
            "context_id": self.context_id,
            "context_kind": self.context_kind,
            "speakers": [self.speaker_id],
            "witnesses": list(self.witnesses),
            "visibility": self.visibility,
            "at": self.at.isoformat(),
        }

    def as_engram(self) -> Engram:
        """Buffer hits are rendered like L0 memories."""
        return Engram(
            engram_id=self.staging_id,
            scope=self.scope,
            kind="episodic",
            raw_text=self.text,
            level=0,
            fidelity=0,
            created_at=self.at,
            refs=list(self.refs),
            meta={"layer": "buffer", "source": self.source(), "status": self.status},
        )


@dataclass
class Entity:
    """A long-lived entity card: a participant or a context."""

    scope: str = "default"
    entity_id: str = ""
    kind: str = "participant"  # participant | context
    name: str = ""
    familiarity: float = 0.0
    interactions: int = 0
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    contexts: List[str] = field(default_factory=list)
    card: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Engram:
    """One memory trace.

    Two axes, deliberately separate:

    ``level``
        How clearly the trace surfaces *now*: 0 verbatim .. 4 archived.
    ``fidelity``
        How much detail is still stored. Monotonic: once detail is gone it
        never comes back, even if a recall makes the trace easier to reach.

    Invariant: ``level >= fidelity``.
    """

    engram_id: str = field(default_factory=new_id)
    scope: str = "default"
    kind: str = "episodic"  # episodic | semantic
    raw_text: str = ""
    gist: str = ""
    gist_source: str = "none"  # none | extractive | llm
    emotion: str = ""
    level: int = 0
    fidelity: int = 0
    importance: float = 0.5
    stability: float = 1.0
    difficulty: float = 5.0
    active_age_days: float = 0.0
    recall_count: int = 0
    created_at: datetime = field(default_factory=utcnow)
    last_recall_at: Optional[datetime] = None
    last_mention_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None
    corrected_at: Optional[datetime] = None
    refs: List[str] = field(default_factory=list)
    tokens: Set[str] = field(default_factory=set)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def actor(self) -> str:
        return str(self.meta.get("actor", "user"))

    @property
    def archived(self) -> bool:
        return self.level >= 4

    def is_archived(self, max_level: int) -> bool:
        return self.level >= max_level

    def age_days(self, now: datetime) -> float:
        return max(0.0, (now - self.created_at).total_seconds() / 86400.0)

    def render(self, now: Optional[datetime] = None) -> str:
        """How the memory surfaces in a prompt.

        This is the single most important method in the library: the model
        must not speak verbatim text for a half-forgotten memory.
        """
        if not self.raw_text and not self.gist:
            return ""
        if self.level <= 0:
            return self.raw_text
        if self.level == 1:
            # An LLM gist is already compressed; allow it room. The extractive
            # fallback stays short because it is only a truncation.
            return self._summary(limit=160 if self.gist_source == "llm" else 48)
        if self.level == 2:
            return _short(
                self._summary(limit=160 if self.gist_source == "llm" else 48), 40
            )
        if self.level == 3:
            when = _vague_period(self.created_at, now or utcnow())
            mood = self.emotion or "你状态不太一样"
            return "隐约记得{}那阵子{}，{}".format(
                when, mood, _l3_tail(self.engram_id)
            )
        return ""

    def _summary(self, limit: int = 48) -> str:
        """Best available condensed text.

        A real LLM summary is used when present. Otherwise we fall back to an
        *extractive* truncation -- never to the full original, because that
        would silently undo graded forgetting.
        """
        if self.gist_source == "llm" and self.gist:
            return self.gist[:limit]
        if self.gist:
            return self.gist[:limit]
        return first_clause(self.raw_text, max_chars=limit)


@dataclass
class RecallHit:
    """What ``recall()`` hands back to the caller."""

    engram: Engram
    score: float
    text: str
    signals: Dict[str, float] = field(default_factory=dict)

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


@dataclass
class Fact:
    """A fine-grained fact with a lifecycle and a source."""

    fact_id: str = field(default_factory=lambda: new_id("fact"))
    scope: str = "default"
    kind: str = "fact"  # fact | preference | plan | relation
    subject: str = ""
    predicate: str = ""
    obj: str = ""
    text: str = ""
    confidence: float = 0.7
    importance: float = 0.5
    status: str = "active"  # active | superseded | expired | forgotten
    ttl_days: float = 180.0
    expires_at: Optional[datetime] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    source_engram: str = ""
    source_refs: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass
class TickReport:
    days: int = 0
    factor_active: float = 0.0
    aged: int = 0
    level_changes: int = 0
    fidelity_changes: int = 0
    scope: str = ""


@dataclass
class Summary:
    """Structured output of a summarizer (LLM or rule based)."""

    gist: str = ""
    key_facts: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)
    sentiment: str = "neutral"
    importance: float = 0.5
    emotion: str = ""


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


def _short(text: str, n: int = 20) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"
