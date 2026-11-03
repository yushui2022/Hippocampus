"""Accumulated self-model: personality and relationship, grown from dialogue.

Episodic memory answers "what happened"; this table answers "who am I becoming,
and who are you to me". It is deliberately separate from the persona prompt:
the prompt is only a seed, this state is updated from real interactions.

v3 adds two things the v2 version got wrong:

* a **waterline** (``reflected_until``) instead of a capped id list, so a busy
  week cannot silently skip memories;
* **evidence counts**, so one joke cannot freeze into a personality trait.

``user_facts`` is legacy: people knowledge belongs in entity cards now, and
reflection no longer generates it. It still renders if an old database has it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from .models import Engram
from .store import Store

_KEYS = ("traits", "inside_jokes", "relationship", "style_notes")
_LEGACY_KEYS = ("user_facts",)
_ALL_KEYS = _KEYS + _LEGACY_KEYS

_LABELS = {
    "traits": "你逐渐形成的性格",
    "user_facts": "你对对方的了解",
    "inside_jokes": "你们之间的梗",
    "relationship": "你们的关系",
    "style_notes": "你说话的习惯",
}


@dataclass
class SelfModel:
    store: Store
    scope: str
    state: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.state:
            self.load()

    def load(self) -> Dict[str, Any]:
        raw = self.store.get_meta("self_model:" + self.scope, "{}")
        try:
            self.state = json.loads(raw)
        except (TypeError, ValueError):
            self.state = {}
        for key in _ALL_KEYS:
            value = self.state.get(key)
            if not isinstance(value, list):
                self.state[key] = []
        evidence = self.state.get("evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        # Backfill: old state had no counts, so every line starts at 1.
        for key in _ALL_KEYS:
            bucket = evidence.get(key)
            if not isinstance(bucket, dict):
                bucket = {}
            for item in self.state.get(key, []):
                bucket.setdefault(str(item), 1)
            evidence[key] = bucket
        self.state["evidence"] = evidence
        return self.state

    def save(self) -> None:
        self.state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.store.set_meta(
            "self_model:" + self.scope,
            json.dumps(self.state, ensure_ascii=False),
        )

    # -- evidence ------------------------------------------------------------
    def evidence(self, key: str, text: str) -> int:
        return int(self.state.get("evidence", {}).get(key, {}).get(text, 0))

    def merge(self, incoming: Dict[str, Any], limit: int = 16) -> None:
        """Merge one reflection. Repeated appearances raise the evidence count."""
        evidence = self.state.setdefault("evidence", {})
        for key in _ALL_KEYS:
            values = incoming.get(key)
            if not isinstance(values, list):
                continue
            bucket = evidence.setdefault(key, {})
            for item in values:
                text = " ".join(str(item).split()).strip()[:60]
                if not text:
                    continue
                bucket[text] = int(bucket.get(text, 0)) + 1
            self.state[key] = _ranked(bucket, limit)
        self.save()

    def render(self, max_items: int = 6, min_evidence: int = 1) -> str:
        """Render stable entries. Brain passes the profile's evidence floor."""
        lines: List[str] = []
        evidence = self.state.get("evidence", {})
        for key in _ALL_KEYS:
            bucket = evidence.get(key, {})
            values = [
                text
                for text in self.state.get(key, [])
                if int(bucket.get(text, 0)) >= max(1, min_evidence)
            ]
            values.sort(key=lambda text: int(bucket.get(text, 0)), reverse=True)
            if values:
                lines.append(
                    "{}：{}".format(_LABELS[key], "；".join(values[:max_items]))
                )
        if not lines:
            return ""
        return "<自我认知>\n" + "\n".join(lines) + "\n</自我认知>"

    # -- waterline -----------------------------------------------------------
    def recent_ids(self) -> set:
        return set(self.state.get("reflected_ids", []))

    def reflected_until(self) -> Optional[datetime]:
        raw = self.state.get("reflected_until")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None

    def mark_reflected(self, engrams: Iterable[Engram]) -> None:
        items = list(engrams)
        if not items:
            return
        newest = max(e.created_at for e in items)
        # Keep ids only for the boundary timestamp (ties cannot be ordered).
        self.state["reflected_ids"] = [
            e.engram_id for e in items if e.created_at >= newest
        ][:64]
        self.state["reflected_until"] = newest.isoformat()
        self.state["reflected_at"] = datetime.now(timezone.utc).isoformat()
        self.save()

    def pending(self, engrams: Iterable[Engram]) -> List[Engram]:
        """Everything newer than the waterline (and not already reflected)."""
        until = self.reflected_until()
        seen = self.recent_ids()
        out: List[Engram] = []
        for engram in engrams:
            if engram.engram_id in seen:
                continue
            if until is not None and engram.created_at <= until:
                continue
            out.append(engram)
        return out


def _ranked(bucket: Dict[str, int], limit: int) -> List[str]:
    items = sorted(bucket.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    return [text for text, _ in items[:limit]]


def reflection_prompt(model: SelfModel, engrams: Iterable[Engram]) -> List[Dict[str, str]]:
    existing = model.render(max_items=10) or "（还没有形成稳定的自我认知）"
    experiences = []
    for engram in engrams:
        text = engram.gist or engram.raw_text
        text = (text or "").strip()
        if text:
            experiences.append("- " + text[:220])
    return [
        {
            "role": "system",
            "content": (
                "你在和对方长期相处，需要更新你正在形成的自我认知。\n"
                "只记录反复出现、稳定的内容；一次性的情绪和事件不要写。\n"
                "不要编造。每条不超过 20 字。\n"
                "对方的信息不要写在这里（它属于人物卡），只写你自己。\n"
                '输出 JSON：{"traits":[],"inside_jokes":[],'
                '"relationship":[],"style_notes":[]}\n'
                "traits=你的性格/习惯；inside_jokes=你们之间的梗；"
                "relationship=关系状态；style_notes=你说话的方式。"
            ),
        },
        {
            "role": "user",
            "content": "已有的自我认知：\n{}\n\n最近的经历：\n{}".format(
                existing, "\n".join(experiences[-24:])
            ),
        },
    ]


def parse_reflection(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        return {}
    return {key: data.get(key) for key in _ALL_KEYS if isinstance(data.get(key), list)}
