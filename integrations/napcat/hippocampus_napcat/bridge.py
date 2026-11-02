from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import Optional, Set

from aiohttp import ClientSession

from .chat import ChatService
from .config import Config
from .events import MessageEvent, parse_message, should_interject
from .memory import MemoryService
from .onebot import OneBotConnection
from .tools import WebTools


class Bridge:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.memory = MemoryService(cfg)
        self.onebot = OneBotConnection()
        self.tools = WebTools(cfg, self.session)
        self.chat = ChatService(
            cfg, self.memory, self.tools, self.onebot, self.session
        )
        self.seen: Set[str] = set()
        self._session: Optional[ClientSession] = None

    async def session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession()
        return self._session

    async def handle(self, data: dict) -> None:
        event = parse_message(data)
        if event is None:
            return
        if event.message_id:
            if event.message_id in self.seen:
                return
            self.seen.add(event.message_id)
            if len(self.seen) > 1200:
                self.seen = set(list(self.seen)[-600:])

        if event.text.startswith("/hmem"):
            await self.command(event)
            return

        addressed = event.addressed(self.cfg.bot_names)
        interject = should_interject(event, self.cfg, random.random())
        mentioned = addressed or interject
        if (
            event.is_group
            and mentioned
            and not event.at_self
            and any(name in event.text for name in self.cfg.bot_names)
            and random.random() > self.cfg.name_chance
        ):
            mentioned = False

        if self.cfg.should_capture(event.group_id, mentioned):
            self.memory.record(
                event.scope,
                event.text or ("[图片]" if event.images else ""),
                sender_name=event.sender_name,
                refs=[event.message_id],
            )
        self.chat.observe(event)

        if not mentioned:
            return
        empty = bool(event.is_group and event.at_self and not event.text)
        await self.chat.reply(event, empty_mention=empty)

    async def command(self, event: MessageEvent) -> None:
        parts = event.text.split(maxsplit=2)
        action = parts[1].lower() if len(parts) > 1 else "status"
        hippo = self.memory.hippo(event.scope)
        if action == "flush":
            hippo.flush_episode()
            body = "已写入记忆。"
        elif action == "recall":
            query = parts[2] if len(parts) > 2 else ""
            if not query:
                body = "用法：/hmem recall 关键词"
            else:
                hits = self.memory.recall(event.scope, query, 6)
                body = "\n".join(
                    "L{} {:.2f} {}".format(h.level, h.score, h.text)
                    for h in hits
                ) or "没有找到记忆。"
        elif action == "self":
            body = self.memory.self_model(event.scope).render()
            body = body or "还没有形成稳定的自我认知。"
        elif action == "reflect":
            await self.memory.reflect(
                event.scope,
                force=True,
                min_pending=self.cfg.reflect_min_pending,
            )
            body = self.memory.self_model(event.scope).render()
            body = body or "还没有形成稳定的自我认知。"
        else:
            stats = self.memory.status(event.scope)
            body = (
                "记忆 {} 条，事实 {} 条，等级 {}\n{}".format(
                    stats.get("total"),
                    stats.get("facts"),
                    stats.get("levels"),
                    stats.get("self_model") or "（还没有自我认知）",
                )
            )
        await self.onebot.send_text(
            group_id=event.group_id, user_id=event.user_id, text=body
        )

    async def maintenance(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.maintenance_seconds)
            try:
                self.memory.tick_all()
                for scope in list(self.memory.hippos):
                    try:
                        await self.memory.reflect(
                            scope, min_pending=self.cfg.reflect_min_pending
                        )
                    except Exception:
                        pass
            except Exception:
                pass

    async def close(self) -> None:
        self.memory.close()
        if self._session is not None:
            await self._session.close()
