"""Source monitoring: remembering *where* a thing was learned.

A memory is not only content; it carries who was there and how private it was.
This module answers one question before anything is injected into a prompt:
**may this be said in front of these listeners?**

Rules (v3 spec 7.7):

    speakable ⇔ visibility == public
               or same context
               or present ⊆ allowed(memory)

Legacy engrams written before v3 have no source and stay speakable; that keeps
old databases usable without a migration.
"""

from __future__ import annotations

from typing import Iterable, Optional, Set

PRIVATE = "private"
SHARED = "shared"
PUBLIC = "public"

CONTEXT_BOOST = 0.08
PRESENCE_BOOST = 0.08


def _ids(values: Iterable[str]) -> Set[str]:
    return {str(v) for v in values if v}


def allowed_listeners(
    source: dict, members: Optional[Iterable[str]] = None
) -> Optional[Set[str]]:
    """Who may hear this memory. ``None`` means everyone."""
    if not source:
        return None
    visibility = source.get("visibility") or PRIVATE
    if visibility == PUBLIC:
        return None
    allowed = _ids(source.get("witnesses") or [])
    allowed |= _ids(source.get("speakers") or [])
    if visibility == SHARED:
        allowed |= _ids(members or [])
    return allowed


def speakable(
    source: dict,
    *,
    context_id: str = "",
    present: Optional[Iterable[str]] = None,
    members: Optional[Iterable[str]] = None,
) -> bool:
    if not source:
        return True
    if (source.get("visibility") or PRIVATE) == PUBLIC:
        return True
    if context_id and source.get("context_id") == context_id:
        return True
    allowed = allowed_listeners(source, members)
    if allowed is None:
        return True
    audience = _ids(present or [])
    if not audience:
        return True
    return audience.issubset(allowed)


def boost(
    source: dict,
    *,
    context_id: str = "",
    present: Optional[Iterable[str]] = None,
) -> float:
    """Retrieval bonus for same-situation and same-people memories."""
    if not source:
        return 0.0
    score = 0.0
    if context_id and source.get("context_id") == context_id:
        score += CONTEXT_BOOST
    people = _ids(source.get("speakers") or []) | _ids(source.get("witnesses") or [])
    if people & _ids(present or []):
        score += PRESENCE_BOOST
    return score
