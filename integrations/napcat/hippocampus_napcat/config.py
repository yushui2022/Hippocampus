from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import FrozenSet, Tuple


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").strip().lower() not in {
        "0", "false", "no", "off", "",
    }


def _csv(name: str, default: str = "") -> Tuple[str, ...]:
    return tuple(x.strip() for x in _env(name, default).split(",") if x.strip())


PERSONA_DEFAULT = (
    "你叫大肥鱼，一只蓝色小鲸鱼娘，住在 QQ 里。"
    "有点小脾气，嘴硬但心软，贪吃、爱面子。"
    "不要自称本鲸、本鱼，不要提 AI、模型、助手。"
    "你在 QQ 上和大家闲聊，不是在完成任务。"
)

STYLE_DEFAULT = (
    "\n说话规则：\n"
    "1. 大多数时候一两句就够，20 个字左右；偶尔可以多说两句。\n"
    "2. 回答完可以停，也可以偶尔接一句短的；不要每句都追问。\n"
    "3. 不要用“呀、呢、哦、～”堆可爱，最多一个语气词。\n"
    "4. 被问是不是 AI：回“你猜”或“不是，别问”。\n"
    "5. 不要只回一个字，至少让对方能接话；但别写小作文。\n"
    "6. 不列清单，不说“有什么可以帮你”。\n"
    "7. 群聊里绝不透露私聊内容。\n"
    "8. 需要实时信息/新闻/链接时，调用 web_search 或 web_fetch；不要编造最新信息。\n"
    "9. 语气自然，像熟人聊天；可以嘴硬、可以吐槽，但不要骂人，不要用“杂鱼”“笨蛋”这类称呼。\n"
)


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = 6199

    memory_db: str = "/data/hippocampus/memory.db"
    brain_scope: str = "self:taozi"
    profile: str = "companion"
    card_min_evidence: int = 2
    chat_model: str = "deepseek-flash"
    chat_base: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    summary_model: str = "deepseek-chat"

    embed_base: str = "http://host.docker.internal:11434/v1"
    embed_model: str = "bge-m3"
    embed_dim: int = 1024

    persona: str = PERSONA_DEFAULT
    style: str = STYLE_DEFAULT
    bot_names: Tuple[str, ...] = ("桃子姐姐", "桃子姐")

    test_group: str = "922410298"
    capture_groups: FrozenSet[str] = field(
        default_factory=lambda: frozenset({"922410298"})
    )
    capture_private: bool = True
    active_chance: float = 0.25
    name_chance: float = 0.95
    recall_budget: int = 2
    max_history: int = 14

    web_tools: bool = True
    typing_delay: bool = True
    multi_chance: float = 0.40
    essay_chance: float = 0.12
    max_reply_chars: int = 60
    essay_max_chars: int = 220

    maintenance_seconds: int = 60
    reflect_min_pending: int = 4

    voice_enabled: bool = True
    tts_url: str = "http://host.docker.internal:6211/tts"
    voice_dir: str = "/data/voice"
    voice_path_prefix: str = "/AstrBot/data/voice"
    voice_chance: float = 1.0
    voice_max_chars: int = 220

    @classmethod
    def from_env(cls) -> "Config":
        test_group = _env("TEST_GROUP", "922410298")
        capture = _csv("CAPTURE_GROUPS", test_group)
        return cls(
            host=_env("BRIDGE_HOST", "0.0.0.0"),
            port=_int("BRIDGE_PORT", 6199),
            memory_db=_env("MEMORY_DB", "/data/hippocampus/memory.db"),
            brain_scope=_env("BRAIN_SCOPE", "self:taozi"),
            profile=_env("BRAIN_PROFILE", "companion"),
            card_min_evidence=_int("CARD_MIN_EVIDENCE", 2),
            chat_model=_env("CHAT_MODEL", "deepseek-flash"),
            chat_base=_env("CHAT_BASE", "https://api.deepseek.com/v1"),
            api_key=_env("DEEPSEEK_API_KEY", ""),
            summary_model=_env("SUMMARY_MODEL", "deepseek-chat"),
            embed_base=_env(
                "EMBED_BASE", "http://host.docker.internal:11434/v1"
            ),
            embed_model=_env("EMBED_MODEL", "bge-m3"),
            embed_dim=_int("EMBED_DIM", 1024),
            persona=_env("PERSONA", PERSONA_DEFAULT),
            style=_env("STYLE", STYLE_DEFAULT),
            bot_names=_csv("BOT_NAMES", "大肥鱼,肥鱼,桃子姐姐,桃子姐"),
            test_group=test_group,
            capture_groups=frozenset(capture or (test_group,)),
            capture_private=_bool("CAPTURE_PRIVATE", True),
            active_chance=_float("ACTIVE_CHANCE", 0.25),
            name_chance=_float("NAME_CHANCE", 0.95),
            recall_budget=_int("RECALL_BUDGET", 2),
            max_history=_int("MAX_HISTORY", 14),
            web_tools=_bool("WEB_TOOLS", True),
            typing_delay=_bool("TYPING_DELAY", True),
            multi_chance=_float("MULTI_CHANCE", 0.40),
            essay_chance=_float("ESSAY_CHANCE", 0.12),
            max_reply_chars=_int("MAX_REPLY_CHARS", 60),
            essay_max_chars=_int("ESSAY_MAX_CHARS", 220),
            maintenance_seconds=_int("MAINTENANCE_SECONDS", 60),
            reflect_min_pending=_int("REFLECT_MIN_PENDING", 4),
            voice_enabled=_bool("VOICE_ENABLED", True),
            tts_url=_env("TTS_URL", "http://host.docker.internal:6211/tts"),
            voice_dir=_env("VOICE_DIR", "/data/voice"),
            voice_path_prefix=_env("VOICE_PATH_PREFIX", "/AstrBot/data/voice"),
            voice_chance=_float("VOICE_CHANCE", 1.0),
            voice_max_chars=_int("VOICE_MAX_CHARS", 220),
        )

    def should_capture(self, group_id: str, addressed: bool) -> bool:
        if not group_id:
            return self.capture_private
        return (
            group_id == self.test_group
            or group_id in self.capture_groups
            or addressed
        )
