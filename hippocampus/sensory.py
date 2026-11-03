"""Sensory gate: most input never reaches memory.

Zero cost, deterministic, no LLM. This is the thalamus: it decides what is
worth the hippocampus's attention *before* anything is written. Rules come
from the v3 spec (7.1) and are deliberately language-level, not
domain-level, so the kernel stays generic.

Addressed input is always kept: being spoken to is an attention signal that
overrides brevity.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import FrozenSet, Iterable, Optional

DEFAULT_FILLERS: FrozenSet[str] = frozenset(
    {
        "嗯",
        "嗯嗯",
        "哦",
        "噢",
        "啊",
        "额",
        "呃",
        "行",
        "好",
        "好的",
        "好吧",
        "好滴",
        "收到",
        "了解",
        "知道了",
        "懂了",
        "可以",
        "没事",
        "没事儿",
        "在",
        "在吗",
        "在不在",
        "哈哈",
        "哈哈哈",
        "哈哈哈哈",
        "嘿嘿",
        "呵呵",
        "草",
        "6",
        "66",
        "666",
        "6666",
        "+1",
        "dd",
        "顶",
        "赞",
        "牛",
        "牛逼",
        "绝了",
        "笑死",
        "真的假的",
        "我去",
        "好家伙",
        "？？？",
        "。。。",
    }
)

_PLACEHOLDER = re.compile(r"^\[[^\]]{1,6}\]$|^【[^】]{1,6}】$")
_WHITESPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)


@dataclass(frozen=True)
class GateDecision:
    keep: bool
    reason: str
    text: str


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return _WHITESPACE.sub(" ", text).strip()


def content_chars(text: str) -> str:
    """What is left once whitespace, punctuation and symbols are removed."""
    return _PUNCT.sub("", normalize(text))


def is_pure_noise(text: str) -> bool:
    stripped = normalize(text)
    if not stripped:
        return True
    if _PLACEHOLDER.match(stripped):
        return True
    return not content_chars(stripped)


def is_filler(text: str, fillers: Iterable[str] = DEFAULT_FILLERS, limit: int = 6) -> bool:
    key = content_chars(text).lower()
    if not key or len(key) > limit:
        return False
    return key in {content_chars(f).lower() for f in fillers}


class SensoryGate:
    def __init__(
        self,
        *,
        min_chars: int = 2,
        fillers: Iterable[str] = DEFAULT_FILLERS,
    ) -> None:
        self.min_chars = max(1, int(min_chars))
        self.fillers: FrozenSet[str] = frozenset(fillers)

    def check(self, text: str, *, addressed: bool = False) -> GateDecision:
        clean = normalize(text)
        if addressed:
            # Attention beats brevity: if she was spoken to, keep it.
            return GateDecision(True, "addressed", clean) if clean else GateDecision(
                False, "empty", clean
            )
        if not clean:
            return GateDecision(False, "empty", clean)
        if is_pure_noise(clean):
            return GateDecision(False, "noise", clean)
        if len(content_chars(clean)) < self.min_chars:
            return GateDecision(False, "too_short", clean)
        if is_filler(clean, self.fillers):
            return GateDecision(False, "filler", clean)
        return GateDecision(True, "ok", clean)


def duplicate_key(text: str) -> str:
    """Normalized form used for spam detection."""
    return content_chars(text).lower()


def recently_said(
    previous: Optional[str], previous_at, now, *, minutes: float = 10.0
) -> bool:
    """True when the same text was repeated inside the spam window."""
    if not previous or previous_at is None:
        return False
    delta = (now - previous_at).total_seconds()
    return 0 <= delta <= minutes * 60
