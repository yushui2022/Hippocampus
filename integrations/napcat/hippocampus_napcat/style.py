from __future__ import annotations

import random
import re


def is_greeting(text: str) -> bool:
    cleaned = re.sub(r"[\s，。！？!?~～、,.]+", "", text or "")
    return cleaned in {
        "你好", "您好", "hi", "hello", "嗨", "哈喽",
        "在吗", "在不在", "在么", "在嘛", "早上好", "早安", "早",
        "晚上好", "晚安", "睡了", "谢谢", "多谢",
    }


def greeting_reply(text: str) -> str:
    cleaned = re.sub(r"[\s，。！？!?~～、,.]+", "", text or "")
    if cleaned in {"晚安", "睡了"}:
        return random.choice(["睡吧。", "晚安。"])
    if cleaned in {"谢谢", "多谢"}:
        return random.choice(["嗯。", "记着就行。"])
    if cleaned in {"早上好", "早安", "早"}:
        return "早。"
    if cleaned in {"在吗", "在不在", "在么", "在嘛"}:
        return random.choice(["在。咋了", "在呢，说。", "在，啥事。"])
    return random.choice(["在。咋了", "哟，今天这么客气。", "在呢。", "嗯？说吧。"])


def clean_reply(text: str, user_text: str = "", max_chars: int = 60) -> str:
    clean = re.sub(r"本鲸|本鱼|大肥鱼", "我", text or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    for phrase in (
        "有什么可以帮", "希望能帮", "如果你愿意", "你想聊点",
        "你想试试", "你今天怎么突然",
        r"作为一?个?\s*(AI|人工智能|模型|助手)",
    ):
        clean = re.sub(phrase, "", clean)
    clean = clean.strip("，。；、 ")
    clean = re.sub(r"[呀呢哦～]+$", "", clean).strip()
    lowered = (user_text or "").lower()
    if any(w in lowered for w in ("ai", "模型", "机器人")) and any(
        w in clean for w in ("桃子姐姐", "模型", "AI", "ai")
    ):
        return random.choice(["你猜。", "不是，别问。", "问这个干嘛。"])
    return clean


def split_reply(
    text: str,
    *,
    max_chars: int = 60,
    multi_chance: float = 0.40,
    essay_chance: float = 0.12,
    essay_max_chars: int = 220,
    rand: float | None = None,
    rand2: float | None = None,
):
    """Return (first bubble, extra bubbles)."""
    if not text:
        return "", []
    essay_roll = random.random() if rand is None else rand
    multi_roll = random.random() if rand2 is None else rand2
    if len(text) >= 50 and essay_roll < essay_chance:
        return text[:essay_max_chars], []
    parts = [p.strip() for p in re.split(r"(?<=[。！？!?])", text) if p.strip()]
    if not parts:
        return text[:max_chars], []
    if len(parts) >= 2 and multi_roll < multi_chance:
        # Spread whole sentences across at most three bubbles.
        group_size = max(1, (len(parts) + 2) // 3)
        chunks = [
            "".join(parts[i : i + group_size]).strip()
            for i in range(0, len(parts), group_size)
        ]
        chunks = [c for c in chunks if c][:3]
        if len(chunks) >= 2:
            return chunks[0][:max_chars], chunks[1:]
    out = parts[0]
    if len(out) < 8 and len(parts) > 1:
        out += parts[1]
    return out[:max_chars], []
