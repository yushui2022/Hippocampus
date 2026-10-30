"""Importance scoring and retrieval ranking.

Importance is a blend, not an LLM call. Rules run on every message; the LLM
only sees the 5-10% that survived the rules. This keeps cost bounded and
stops scores from drifting between runs.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Set

from .decay import retrievability
from .tokens import coverage, jaccard

EXPLICIT_RE = re.compile(
    r"记住|别忘了|别忘记|不要忘|约定|发誓|答应我|以后每年|remember|don'?t forget",
    re.IGNORECASE,
)
SELF_WORDS = ("我", "你", "我们", "咱", "自己", "me", "you", "we")

# A deliberately small, transparent lexicon. v1 should learn this from data.
EMOTION_LEXICON: Dict[str, tuple] = {
    "难过": ("难过", "伤心", "哭", "崩溃", "委屈", "失落", "emo", "想哭", "难受"),
    "焦虑": ("焦虑", "害怕", "担心", "紧张", "失眠", "压力", "慌", "不安"),
    "愤怒": ("生气", "愤怒", "气死", "烦死", "讨厌", "恶心", "吵架", "骂"),
    "开心": ("开心", "高兴", "太好了", "幸福", "激动", "爽", "哈哈", "惊喜", "喜欢"),
    "孤独": ("孤独", "一个人", "没人", "寂寞", "空落落"),
    "疲惫": ("累", "疲倦", "撑不住", "没力气", "加班", "熬夜"),
}

_EMOTION_INDEX = {}
for _label, _words in EMOTION_LEXICON.items():
    for _w in _words:
        _EMOTION_INDEX[_w] = _label

# "Substance": does this message contain something a person could retell?
# A life event, a named person, a number, a place. Trivia like "咖啡凉了" has
# none of these, and that is exactly what makes it forgettable.
LIFE_EVENT_WORDS = (
    "离婚", "结婚", "裁员", "确诊", "住院", "去世", "走了", "生病", "手术",
    "考砸", "考试", "高考", "考研", "面试", "入职", "辞职", "搬家", "分手",
    "毕业", "怀孕", "生日", "约定", "答应", "offer", "吵架", "报警", "欠债",
)
KIN_WORDS = (
    "爸", "妈", "奶奶", "爷爷", "外婆", "外公", "哥", "姐", "弟", "妹",
    "男朋友", "女朋友", "老公", "老婆", "孩子",
)
PLACE_RE = re.compile(r"北京|上海|广州|深圳|成都|杭州|南京|武汉|西安|重庆|长沙|苏州")
NUMBER_RE = re.compile(r"\d+")


def substance_score(text: str) -> float:
    """How retellable the message is: events, people, places, numbers."""
    raw = text or ""
    lowered = raw.lower()
    score = 0.0
    if any(w in raw for w in LIFE_EVENT_WORDS):
        score += 0.6
    if any(w in raw for w in KIN_WORDS):
        score += 0.25
    if PLACE_RE.search(raw):
        score += 0.15
    if NUMBER_RE.search(raw) or "岁" in raw or "月" in raw:
        score += 0.1
    return min(1.0, score)


@dataclass
class ImportanceReport:
    value: float
    features: Dict[str, float] = field(default_factory=dict)


def detect_emotion(text: str) -> str:
    text = (text or "").lower()
    best_label = ""
    best_hits = 0
    for label, words in EMOTION_LEXICON.items():
        hits = sum(1 for w in words if w in text)
        if hits > best_hits:
            best_hits = hits
            best_label = label
    return best_label


def score_importance(
    text: str,
    tokens: Set[str],
    weights: Dict[str, float],
    profile_weight_sum: float,
    *,
    novelty: float,
    repetition: float,
    llm_score: Optional[float] = None,
) -> ImportanceReport:
    """Combine cheap signals into a single importance in [0, 1]."""
    features: Dict[str, float] = {}

    features["explicit"] = 1.0 if EXPLICIT_RE.search(text or "") else 0.0

    emotion_hits = sum(1 for w in _EMOTION_INDEX if w in (text or "").lower())
    punctuation = (text or "").count("！") + (text or "").count("!") + (text or "").count("？")
    features["emotion"] = min(1.0, emotion_hits / 3.0 + min(punctuation, 3) * 0.1)

    self_hits = sum(1 for w in SELF_WORDS if w in (text or "").lower())
    features["self"] = min(1.0, self_hits / 3.0)

    features["novelty"] = max(0.0, min(1.0, novelty))
    features["repetition"] = max(0.0, min(1.0, repetition))
    features["substance"] = substance_score(text)

    if llm_score is None:
        # No LLM available: drop its weight instead of inventing a neutral value,
        # otherwise every memory quietly scores 0.5-ish and nothing stands out.
        weights = {k: v for k, v in weights.items() if k != "llm"}
        total = sum(weights.values()) or 1.0
        raw = sum(weights.get(k, 0.0) * features.get(k, 0.0) for k in weights)
        value = raw / total
    else:
        features["llm"] = max(0.0, min(1.0, llm_score))
        total = profile_weight_sum or 1.0
        value = sum(weights.get(k, 0.0) * features.get(k, 0.0) for k in weights) / total

    return ImportanceReport(value=max(0.0, min(1.0, value)), features=features)


@dataclass
class RetrievalSignals:
    similarity: float = 0.0
    retrievability: float = 0.0
    importance: float = 0.0
    recency: float = 0.0
    relationship: float = 0.0
    emotion: float = 0.0
    cooldown: float = 0.0  # 1.0 = recently mentioned, penalise


def retrieval_score(
    signals: RetrievalSignals,
    weights: Dict[str, float],
    cooldown_weight: float = 0.25,
) -> float:
    total = sum(weights.values()) or 1.0
    positives = 0.0
    for key, weight in weights.items():
        if weight <= 0:
            continue
        positives += weight * getattr(signals, key, 0.0)
    return positives / total - cooldown_weight * signals.cooldown


def recency_factor(created_at: datetime, now: datetime, horizon_days: float = 30.0) -> float:
    days = max(0.0, (now - created_at).total_seconds() / 86400.0)
    return math.exp(-days / max(horizon_days, 1e-6))


def similarity(query_tokens: Set[str], doc_tokens: Set[str]) -> float:
    """Blend of coverage and jaccard: coverage rewards precision, jaccard
    stops very long messages from winning purely by length."""
    return 0.65 * coverage(query_tokens, doc_tokens) + 0.35 * jaccard(query_tokens, doc_tokens)


def llm_scorer(score: Callable[[str], Any]) -> Callable[[str], Optional[float]]:
    """Adapter so callers can pass any callable returning a number."""

    def _wrapped(text: str) -> Optional[float]:
        try:
            value = score(text)
        except Exception:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return _wrapped
