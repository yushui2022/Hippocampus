"""Entity cards: who people are, and what a place is like.

Evidence is the point. A single joke does not become a personality trait; a
note needs ``card_min_evidence`` independent events (or one very salient one)
before it is rendered. This is the fix for "one offhand line froze into canon".

Cards live in ``entities.card``::

    {"notes": [{"text": ..., "count": 2, "evidence": ["eng_..."],
                "first": iso, "last": iso, "salience": 0.9}],
     "topics": {"面试": 3}}
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import Entity

MAX_NOTES = 40
MAX_EVIDENCE = 8


def add_note(
    card: Dict[str, Any],
    text: str,
    *,
    evidence: Optional[str] = None,
    at: Optional[datetime] = None,
    salience: float = 0.0,
) -> bool:
    """Record one observation. Returns True when it is new."""
    clean = " ".join((text or "").split()).strip()[:80]
    if not clean:
        return False
    notes = card.setdefault("notes", [])
    stamp = at.isoformat() if at else None
    for note in notes:
        if note.get("text") == clean:
            note["count"] = int(note.get("count", 1)) + 1
            note["last"] = stamp
            note["salience"] = max(float(note.get("salience", 0.0)), float(salience))
            if evidence:
                refs = note.setdefault("evidence", [])
                if evidence not in refs:
                    refs.append(evidence)
                    note["evidence"] = refs[-MAX_EVIDENCE:]
            return False
    notes.append(
        {
            "text": clean,
            "count": 1,
            "evidence": [evidence][:1] if evidence else [],
            "first": stamp,
            "last": stamp,
            "salience": float(salience),
        }
    )
    if len(notes) > MAX_NOTES:
        # Keep the best-supported notes, not just the newest.
        notes.sort(key=lambda n: (int(n.get("count", 1)), n.get("last") or ""), reverse=True)
        del notes[MAX_NOTES:]
    return True


def add_topic(card: Dict[str, Any], topic: str, *, at: Optional[datetime] = None) -> None:
    clean = (topic or "").strip()[:24]
    if not clean:
        return
    topics = card.setdefault("topics", {})
    topics[clean] = int(topics.get(clean, 0)) + 1
    if len(topics) > 40:
        keep = sorted(topics.items(), key=lambda kv: kv[1], reverse=True)[:30]
        card["topics"] = dict(keep)


def stable_notes(
    card: Dict[str, Any], min_evidence: int = 2, *, high_salience: float = 0.85
) -> List[Dict[str, Any]]:
    out = []
    for note in card.get("notes", []) or []:
        count = int(note.get("count", 1))
        if count >= max(1, min_evidence) or float(note.get("salience", 0.0)) >= high_salience:
            out.append(note)
    out.sort(
        key=lambda n: (int(n.get("count", 1)), n.get("last") or ""),
        reverse=True,
    )
    return out


def familiarity_label(value: float) -> str:
    if value < 0.15:
        return "不太熟"
    if value < 0.4:
        return "有点印象"
    if value < 0.7:
        return "挺熟"
    return "很熟"


def render_person(
    entity: Entity, *, min_evidence: int = 2, max_items: int = 6
) -> str:
    notes = stable_notes(entity.card, min_evidence)
    if not notes:
        return ""
    label = familiarity_label(entity.familiarity)
    body = "；".join(note["text"] for note in notes[:max_items])
    name = entity.name or entity.entity_id
    return "{}：{}（{}）".format(name, body, label)


def render_context(
    entity: Entity, *, min_evidence: int = 2, max_items: int = 5
) -> str:
    topics = entity.card.get("topics") or {}
    ranked = sorted(topics.items(), key=lambda kv: kv[1], reverse=True)
    ranked = [(k, v) for k, v in ranked if v >= min_evidence][:max_items]
    if not ranked:
        return ""
    name = entity.name or entity.entity_id
    return "{}：常聊 {}".format(name, "、".join(k for k, _ in ranked))


def decayed_familiarity(
    familiarity: float, last_seen: Optional[datetime], now: datetime, half_life_days: float
) -> float:
    if last_seen is None or half_life_days <= 0:
        return max(0.0, min(1.0, familiarity))
    days = max(0.0, (now - last_seen).total_seconds() / 86400.0)
    value = familiarity * (0.5 ** (days / half_life_days))
    return max(0.0, min(1.0, value))
