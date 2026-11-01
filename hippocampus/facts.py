"""Fine-grained facts with a lifecycle and a mandatory source.

Hard rule: facts are extracted **only from user utterances**. Assistant text
never becomes a fact, which is what keeps a model's own hallucination from
being replayed as memory later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Protocol

from .models import Engram, Fact

TTL_DAYS = {
    "plan": 2.0,
    "fact": 180.0,
    "preference": 60.0,
    "relation": 90.0,
}

_CONFIDENCE_FLOOR = 0.6

_PREF = re.compile(
    r"我(?:很|特别|有点|不太)?(喜欢|爱|讨厌|怕|不吃|不喝|吃|喝|玩)([^，。！？,.!?\s]{1,15})"
)
_NAME = re.compile(r"我叫([^，。！？,.!?\s]{1,10})")
_LIVE = re.compile(r"我(?:现在|最近)?住在([^，。！？,.!?\s]{1,12})")
_WORK = re.compile(r"我在([^，。！？,.!?\s]{1,12})(?:工作|上班)")
_PET = re.compile(r"我养了?([^，。！？,.!?\s]{1,12})")
_PLAN = re.compile(r"我(明天|后天|下周|下个月|明年)([^，。！？,.!?]{2,20})")


@dataclass
class FactCandidate:
    kind: str
    subject: str
    predicate: str
    obj: str
    text: str
    confidence: float = 0.7
    valid_from: Optional[datetime] = None


class FactExtractor(Protocol):
    def extract(self, engram: Engram) -> List[FactCandidate]:
        ...


class RuleFactExtractor:
    """Transparent, dependency-free extraction for the common companion cases."""

    def extract(self, engram: Engram) -> List[FactCandidate]:
        if engram.actor != "user":
            return []
        text = engram.raw_text or ""
        out: List[FactCandidate] = []

        for match in _PREF.finditer(text):
            verb, obj = match.group(1), match.group(2).strip()
            if obj:
                out.append(
                    FactCandidate(
                        kind="preference",
                        subject="主人",
                        predicate=verb,
                        obj=obj,
                        text=match.group(0),
                        confidence=0.75,
                    )
                )

        for pattern, kind, predicate in (
            (_NAME, "fact", "名字"),
            (_LIVE, "fact", "住在"),
            (_WORK, "fact", "工作在"),
            (_PET, "relation", "养"),
        ):
            for match in pattern.finditer(text):
                obj = match.group(1).strip()
                if obj:
                    out.append(
                        FactCandidate(
                            kind=kind,
                            subject="主人",
                            predicate=predicate,
                            obj=obj,
                            text=match.group(0),
                            confidence=0.7,
                        )
                    )

        for match in _PLAN.finditer(text):
            when, what = match.group(1), match.group(2).strip()
            if what:
                out.append(
                    FactCandidate(
                        kind="plan",
                        subject="主人",
                        predicate=when,
                        obj=what,
                        text=match.group(0),
                        confidence=0.65,
                    )
                )

        return _dedupe(out)


class NullFactExtractor:
    def extract(self, engram: Engram) -> List[FactCandidate]:
        return []


def build_fact(
    candidate: FactCandidate,
    *,
    scope: str,
    source_engram: str,
    source_refs: Iterable[str],
    now: Optional[datetime] = None,
) -> Optional[Fact]:
    """Validate a candidate and attach TTL / source."""
    if not candidate.obj.strip() or not candidate.text.strip():
        return None
    if candidate.confidence < _CONFIDENCE_FLOOR:
        return None
    at = now or datetime.now(timezone.utc)
    ttl = TTL_DAYS.get(candidate.kind, TTL_DAYS["fact"])
    return Fact(
        scope=scope,
        kind=candidate.kind,
        subject=candidate.subject or "主人",
        predicate=candidate.predicate,
        obj=candidate.obj,
        text=candidate.text,
        confidence=candidate.confidence,
        importance=min(1.0, 0.5 + candidate.confidence / 2.0),
        ttl_days=ttl,
        expires_at=at + timedelta(days=ttl),
        valid_from=candidate.valid_from or at,
        source_engram=source_engram,
        source_refs=list(source_refs),
        created_at=at,
        updated_at=at,
    )


def _dedupe(candidates: List[FactCandidate]) -> List[FactCandidate]:
    seen = set()
    out: List[FactCandidate] = []
    for candidate in candidates:
        key = (candidate.kind, candidate.predicate, candidate.obj)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out
