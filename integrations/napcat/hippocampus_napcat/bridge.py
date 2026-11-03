from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

from aiohttp import ClientSession

from .chat import ChatService
from .config import Config
from .events import MessageEvent, parse_message, should_interject
from .memory import MemoryService
from .onebot import OneBotConnection
from .tools import WebTools


def log(*args) -> None:
    print("[napcat-bridge {}]".format(time.strftime("%H:%M:%S")), *args, flush=True)


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
        self._speakers: Dict[str, Dict[str, datetime]] = defaultdict(dict)
        self._session: Optional[ClientSession] = None

    async def session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession()
        return self._session

    # -- situation bookkeeping ----------------------------------------------
    def _note_speaker(self, context_id: str, speaker_id: str, at: datetime) -> None:
        self._speakers[context_id][speaker_id] = at
        if len(self._speakers[context_id]) > 200:
            cutoff = at - timedelta(hours=24)
            self._speakers[context_id] = {
                key: value
                for key, value in self._speakers[context_id].items()
                if value >= cutoff
            }

    def _witnesses(self, context_id: str, at: datetime) -> List[str]:
        cutoff = at - timedelta(minutes=30)
        return [
            speaker
            for speaker, seen in self._speakers[context_id].items()
            if seen >= cutoff
        ]

    def _active_size(self, context_id: str, at: datetime) -> int:
        cutoff = at - timedelta(hours=24)
        return max(
            2,
            sum(
                1
                for seen in self._speakers[context_id].values()
                if seen >= cutoff
            ),
        )

    # -- events --------------------------------------------------------------
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
        log("recv", event.context_id, event.sender_name, (event.text or "[图片]")[:40])

        if event.text.startswith("/voice"):
            text = event.text[len("/voice") :].strip()
            ok = await self.chat.speak_to(
                group_id=event.group_id,
                user_id=event.user_id,
                text=text or "这是桃子姐姐的语音测试。",
            )
            await self.onebot.send_text(
                group_id=event.group_id,
                user_id=event.user_id,
                text="语音发送成功" if ok else "语音发送失败",
            )
            return

        if event.text.startswith("/hmem"):
            await self.command(event)
            return

        now = datetime.now(timezone.utc)
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

        witnesses = self._witnesses(event.context_id, now)
        self._note_speaker(event.context_id, event.speaker_id, now)

        if self.cfg.should_capture(event.group_id, addressed):
            try:
                self.memory.observe(
                    event,
                    witnesses=witnesses or [event.speaker_id],
                    context_size=self._active_size(event.context_id, now),
                )
            except Exception as exc:  # noqa: BLE001
                log("observe failed", repr(exc))
        self.chat.observe(event)

        if not mentioned:
            return
        empty = bool(event.is_group and event.at_self and not event.text)
        log("reply", event.context_id)
        await self.chat.reply(event, empty_mention=empty)

    # -- commands ------------------------------------------------------------
    async def command(self, event: MessageEvent) -> None:
        parts = event.text.split(maxsplit=2)
        action = parts[1].lower() if len(parts) > 1 else "status"
        if action == "flush":
            report = self.memory.sleep(context_id=event.context_id, force=True)
            body = "已巩固：晋升 {} 条，丢弃 {} 条。".format(
                report.promoted, report.dropped
            )
        elif action == "recall":
            query = parts[2] if len(parts) > 2 else ""
            if not query:
                body = "用法：/hmem recall 关键词"
            else:
                bundle = self.memory.context(
                    query,
                    context_id=event.context_id,
                    present=[event.speaker_id],
                    budget=6,
                )
                lines = [
                    "L{} {:.2f} {}".format(h.level, h.score, h.text)
                    for h in bundle.memories
                ]
                lines += ["B {:.2f} {}".format(h.score, h.text) for h in bundle.buffer]
                body = "\n".join(lines) or "没有找到记忆。"
        elif action == "self":
            body = self.memory.self_block() or "还没有形成稳定的自我认知。"
        elif action == "reflect":
            self.memory.reflect(force=True)
            body = self.memory.self_block() or "还没有形成稳定的自我认知。"
        else:
            stats = self.memory.status()
            body = (
                "记忆 {total} 条，事实 {facts} 条，等级 {levels}\n"
                "缓冲 {buffer_pending} 待巩固 / {buffer_promoted} 已晋升 / "
                "{buffer_dropped} 已丢弃\n"
                "认识 {entities} 个实体\n"
                "{self_model}"
            ).format(
                total=stats.get("total"),
                facts=stats.get("facts"),
                levels=stats.get("levels"),
                buffer_pending=stats.get("buffer_pending"),
                buffer_promoted=stats.get("buffer_promoted"),
                buffer_dropped=stats.get("buffer_dropped"),
                entities=stats.get("entities"),
                self_model=stats.get("self_model") or "（还没有自我认知）",
            )
        await self.onebot.send_text(
            group_id=event.group_id, user_id=event.user_id, text=body
        )

    # -- background ----------------------------------------------------------
    async def maintenance(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.maintenance_seconds)
            try:
                report = self.memory.maintain()
                if report.get("promoted") or report.get("dropped"):
                    log(
                        "maintain",
                        "promoted={}".format(report.get("promoted")),
                        "dropped={}".format(report.get("dropped")),
                        "purged={}".format(report.get("purged")),
                    )
            except Exception as exc:  # noqa: BLE001
                log("maintain failed", repr(exc))

    async def close(self) -> None:
        self.memory.close()
        if self._session is not None:
            await self._session.close()
