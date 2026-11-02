from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import Config


@dataclass
class MessageEvent:
    self_id: str
    user_id: str
    message_id: str
    sender_name: str
    text: str
    images: List[str] = field(default_factory=list)
    group_id: str = ""
    at_self: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_group(self) -> bool:
        return bool(self.group_id)

    @property
    def scope(self) -> str:
        if self.group_id:
            return "qq:GroupMessage:" + self.group_id
        return "qq:FriendMessage:" + self.user_id

    def addressed(self, names=("桃子姐姐", "桃子姐")) -> bool:
        return (not self.is_group) or self.at_self or any(
            name in self.text for name in names
        )


def parse_message(data: Dict[str, Any]) -> Optional[MessageEvent]:
    """Parse a OneBot v11 message event.

    Returns None for non-message events, malformed events and self messages.
    """
    if data.get("post_type") != "message":
        return None
    self_id = str(data.get("self_id", ""))
    user_id = str(data.get("user_id", ""))
    if not user_id or user_id == self_id:
        return None
    msg_type = data.get("message_type")
    group_id = str(data.get("group_id", "")) if msg_type == "group" else ""
    sender = data.get("sender") or {}
    name = str(sender.get("card") or sender.get("nickname") or user_id)

    texts: List[str] = []
    images: List[str] = []
    at_self = False
    for seg in data.get("message") or []:
        if not isinstance(seg, dict):
            continue
        kind = seg.get("type")
        payload = seg.get("data") or {}
        if kind == "text":
            texts.append(str(payload.get("text", "")))
        elif kind == "at" and str(payload.get("qq", "")) == self_id:
            at_self = True
        elif kind == "image":
            url = payload.get("url") or payload.get("file")
            if url:
                images.append(str(url))
    return MessageEvent(
        self_id=self_id,
        user_id=user_id,
        message_id=str(data.get("message_id", "")),
        sender_name=name,
        text="".join(texts).strip(),
        images=images,
        group_id=group_id,
        at_self=at_self,
        raw=data,
    )


def should_interject(event: MessageEvent, cfg: Config, rand: float) -> bool:
    if not event.is_group or event.group_id != cfg.test_group:
        return False
    if event.at_self or any(n in event.text for n in cfg.bot_names):
        return False
    return bool(event.text) and rand < cfg.active_chance
