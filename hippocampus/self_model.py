"""Accumulated self-model: personality and relationship, grown from dialogue.

Episodic memory answers "what happened"; this table answers "who am I becoming,
and who are you to me". It is deliberately separate from the persona prompt:
the prompt is only a seed, this state is updated from real interactions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from .models import Engram
from .store import Store

_KEYS = ("traits", "user_facts", "inside_jokes", "relationship", "style_notes")


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
        for key in _KEYS:
            value = self.state.get(key)
            if not isinstance(value, list):
                self.state[key] = []
        return self.state

    def save(self) -> None:
        self.state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.store.set_meta(
            "self_model:" + self.scope,
            json.dumps(self.state, ensure_ascii=False),
        )

    def merge(self, incoming: Dict[str, Any], limit: int = 16) -> None:
        for key in _KEYS:
            values = incoming.get(key)
            if not isinstance(values, list):
                continue
            merged: List[str] = []
            seen = set()
            for item in list(values) + list(self.state.get(key, [])):
                text = str(item).strip()[:60]
                if not text or text in seen:
                    continue
                seen.add(text)
                merged.append(text)
            self.state[key] = merged[:limit]
        self.save()

    def render(self, max_items: int = 6) -> str:
        labels = {
            "traits": "你逐渐形成的性格",
            "user_facts": "你对对方的了解",
            "inside_jokes": "你们之间的梗",
            "relationship": "你们的关系",
            "style_notes": "你说话的习惯",
        }
        lines: List[str] = []
        for key in _KEYS:
            values = self.state.get(key, [])[:max_items]
            if values:
                lines.append("{}：{}".format(labels[key], "；".join(values)))
        if not lines:
            return ""
        return "<自我认知>\n" + "\n".join(lines) + "\n</自我认知>"

    def recent_ids(self) -> set:
        return set(self.state.get("reflected_ids", []))

    def mark_reflected(self, engrams: Iterable[Engram]) -> None:
        ids = [e.engram_id for e in engrams]
        merged = list(dict.fromkeys(ids + list(self.state.get("reflected_ids", []))))
        self.state["reflected_ids"] = merged[:200]
        self.save()

    def pending(self, engrams: Iterable[Engram]) -> List[Engram]:
        seen = self.recent_ids()
        return [e for e in engrams if e.engram_id not in seen]


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
                "输出 JSON：{\"traits\":[],\"user_facts\":[],\"inside_jokes\":[],"
                "\"relationship\":[],\"style_notes\":[]}\n"
                "traits=你的性格/习惯；user_facts=对方的稳定偏好；"
                "inside_jokes=你们之间的梗；relationship=关系状态；"
                "style_notes=你说话的方式。"
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
    return {key: data.get(key) for key in _KEYS if isinstance(data.get(key), list)}
