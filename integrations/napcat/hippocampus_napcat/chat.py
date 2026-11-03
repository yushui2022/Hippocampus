from __future__ import annotations

import asyncio
import base64
import json
import random
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import ClientSession

from .config import Config
from .events import MessageEvent
from .memory import MemoryService
from .onebot import OneBotConnection
from .style import clean_reply, greeting_reply, is_greeting, split_reply
from .tools import WebTools


class ChatService:
    """Prompt assembly, model/tool loop, and reply rhythm."""

    def __init__(
        self,
        cfg: Config,
        memory: MemoryService,
        tools: WebTools,
        onebot: OneBotConnection,
        session_getter,
    ) -> None:
        self.cfg = cfg
        self.memory = memory
        self.tools = tools
        self.onebot = onebot
        self._session_getter = session_getter
        self.histories: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=cfg.max_history)
        )

    def observe(self, event: MessageEvent) -> None:
        text = event.text or ("[图片]" if event.images else "")
        if text:
            self.histories[event.context_id].append(
                {"role": "user", "name": event.sender_name, "text": text}
            )

    async def reply(self, event: MessageEvent, *, empty_mention: bool = False) -> None:
        text = event.text
        if is_greeting(text):
            answer = greeting_reply(text)
            await self._send(event, answer)
            self._record_assistant(event, answer)
            return

        query = text
        if empty_mention:
            recent = [
                h["text"]
                for h in list(self.histories[event.context_id])[-4:]
                if h.get("role") == "user"
            ]
            query = " ".join(recent[-3:]) or "最近"

        try:
            bundle = self.memory.context(
                query,
                context_id=event.context_id,
                present=[event.speaker_id],
                budget=self.cfg.recall_budget,
            )
        except Exception:
            bundle = None

        system = self._system(bundle, empty_mention)
        messages: List[dict] = [{"role": "system", "content": system}]
        history = list(self.histories[event.context_id])[-8:]
        for item in history:
            if (
                item.get("role") == "user"
                and item.get("text") == (text or ("[图片]" if event.images else ""))
                and item is history[-1]
            ):
                continue
            prefix = (
                "{}: ".format(item.get("name", ""))
                if item.get("role") == "user"
                else ""
            )
            messages.append(
                {"role": item["role"], "content": prefix + item["text"]}
            )

        current: Any = text
        if event.images:
            parts: List[dict] = [
                {"type": "text", "text": text or "（发了一张图片）"}
            ]
            for url in event.images[:3]:
                data_url = await self._image_data(url)
                if data_url:
                    parts.append(
                        {"type": "image_url", "image_url": {"url": data_url}}
                    )
            current = parts
        messages.append({"role": "user", "content": current})

        try:
            answer = await self._chat(messages)
        except Exception:
            return
        answer = clean_reply(
            answer, text, max_chars=self.cfg.max_reply_chars
        )
        if not answer:
            return
        visible, extras = split_reply(
            answer,
            max_chars=self.cfg.max_reply_chars,
            multi_chance=self.cfg.multi_chance,
            essay_chance=self.cfg.essay_chance,
            essay_max_chars=self.cfg.essay_max_chars,
        )
        if visible:
            if self.cfg.typing_delay:
                await asyncio.sleep(random.uniform(0.3, 1.0))
            await self._send(event, visible)
        for extra in extras:
            await asyncio.sleep(random.uniform(0.6, 1.4))
            await self._send(event, extra)
        full = " ".join([visible] + list(extras)).strip()
        if full:
            self._record_assistant(event, full)

    def _system(self, bundle, empty_mention: bool) -> str:
        parts = [self.cfg.persona, self.cfg.style]
        if bundle is not None:
            rendered = bundle.render()
            if rendered:
                parts.append(rendered)
        self_block = self.memory.self_block()
        if self_block:
            parts.append(self_block)
        if empty_mention:
            parts.append(
                "对方只 @ 了你，没有输入文字。不要问“有什么事/想聊什么”，"
                "优先接上他刚才说的话；不要跳到群里别的旧话题。"
            )
        return "\n".join(part for part in parts if part)

    async def _chat(self, messages: List[dict]) -> str:
        tools = self.tools.schema()
        for _ in range(3):
            payload = {
                "model": self.cfg.chat_model,
                "messages": messages,
                "temperature": 0.9,
                "max_tokens": 900,
                "stream": False,
            }
            if tools:
                payload["tools"] = tools
            session = await self._session_getter()
            async with session.post(
                self.cfg.chat_base.rstrip("/") + "/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + self.cfg.api_key,
                },
                json=payload,
                timeout=120,
            ) as resp:
                body = await resp.json()
            if resp.status >= 400:
                raise RuntimeError("chat error {}: {}".format(resp.status, body))
            choices = body.get("choices") or []
            if not choices:
                raise RuntimeError("no choices")
            message = choices[0].get("message", {})
            calls = message.get("tool_calls") or []
            if not calls:
                return str(message.get("content") or "").strip()
            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": calls,
                }
            )
            for call in calls:
                fn = call.get("function") or {}
                name = str(fn.get("name") or "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await self.tools.run(name, args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": result[:6000],
                    }
                )
        return ""

    async def _send(self, event: MessageEvent, text: str) -> None:
        if not text:
            return
        await self.onebot.send_text(
            group_id=event.group_id, user_id=event.user_id, text=text
        )

    def _record_assistant(self, event: MessageEvent, text: str) -> None:
        try:
            self.memory.observe_assistant(event, text)
        except Exception:
            pass
        self.histories[event.context_id].append(
            {"role": "assistant", "text": text}
        )

    async def _image_data(self, url: str) -> str:
        try:
            if url.startswith("base64://"):
                return "data:image/png;base64," + url[len("base64://") :]
            if url.startswith("http://") or url.startswith("https://"):
                session = await self._session_getter()
                async with session.get(url, timeout=30) as resp:
                    if resp.status != 200:
                        return ""
                    data = await resp.read()
            elif url.startswith("file://"):
                data = Path(url[7:]).read_bytes()
            else:
                return ""
            if len(data) > 6 * 1024 * 1024:
                return ""
            return "data:image/png;base64," + base64.b64encode(data).decode()
        except Exception:
            return ""
