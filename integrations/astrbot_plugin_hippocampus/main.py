from __future__ import annotations

import asyncio
import os
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# The compose file mounts the Hippocampus checkout here.
HIPPO_PATH = os.environ.get("HIPPOCAMPUS_PATH", "/opt/hippocampus")
if Path(HIPPO_PATH).exists() and HIPPO_PATH not in sys.path:
    sys.path.insert(0, HIPPO_PATH)

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.core.agent.message import TextPart
from astrbot.core.message.message_event_result import MessageChain

from hippocampus import (
    Event as HEvent,
    Hippocampus,
    LLMSummarizer,
    LocalEmbedder,
    OpenAIEmbedder,
    Store,
)
from hippocampus.self_model import SelfModel, parse_reflection, reflection_prompt


class HippocampusPlugin(Star):
    """Replace a chat bot's long-term memory with the Hippocampus kernel."""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self.enabled = bool(config.get("enabled", True))
        self.capture_group = bool(config.get("capture_group", True))
        self.recall_budget = int(config.get("recall_budget", 6))
        self.max_chars = int(config.get("max_chars", 2000))
        self.persona = str(config.get("persona", "") or "")
        self.debug = bool(config.get("debug", False))
        self.name_reply_probability = float(
            config.get("name_reply_probability", 0.85)
        )
        self.capture_all_group = bool(config.get("capture_all_group", True))
        self.flush_interval = int(config.get("flush_interval_seconds", 60))
        self.humanize = bool(config.get("humanize", True))
        self.max_reply_chars = int(config.get("max_reply_chars", 40))
        self.typing_delay = bool(config.get("typing_delay", True))
        self.multi_message_chance = float(config.get("multi_message_chance", 0.35))
        self.essay_chance = float(config.get("essay_chance", 0.10))
        self.essay_max_chars = int(config.get("essay_max_chars", 220))
        self.auto_recall = bool(config.get("auto_recall", True))
        self.auto_tick = bool(config.get("auto_tick", True))
        self.auto_recall_cooldown_hours = float(
            config.get("auto_recall_cooldown_hours", 24.0)
        )
        self._flush_task = asyncio.create_task(self._periodic_flush())

        db_path = str(
            config.get("db_path", "/AstrBot/data/hippocampus/memory.db")
        )
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.store = Store(db_path)

        self.embedder = self._build_embedder(config)
        self.summarizer = self._build_summarizer(config)
        self._hippos: Dict[str, Hippocampus] = {}
        self._self_models: Dict[str, SelfModel] = {}
        self._active_days: Dict[str, set] = {}
        self._recorded: set[str] = set()
        self._recalled: set[str] = set()
        logger.info(
            "[hippocampus] ready: db=%s embed=%s summary=%s",
            db_path,
            type(self.embedder).__name__ if self.embedder else "none",
            type(self.summarizer).__name__ if self.summarizer else "none",
        )

    # -- construction --------------------------------------------------------
    def _build_embedder(self, config: AstrBotConfig):
        provider = str(config.get("embed_provider", "local")).lower()
        if provider == "none":
            return None
        model = str(config.get("embed_model", "bge-m3"))
        dim = int(config.get("embed_dim", 1024))
        base = str(
            config.get("embed_base", "http://host.docker.internal:11434/v1")
        )
        if provider == "local":
            return LocalEmbedder(base_url=base, model=model, dim=dim)
        key = str(config.get("embed_api_key", "") or os.environ.get("HIPPO_EMBED_KEY", ""))
        if not key:
            logger.warning("[hippocampus] openai embedder has no key; using BM25")
            return None
        return OpenAIEmbedder(
            api_key=key, base_url=base, model=model, dim=dim
        )

    def _build_summarizer(self, config: AstrBotConfig):
        if not bool(config.get("summarize", True)):
            return None
        key = str(
            config.get("summary_api_key", "")
            or os.environ.get("DEEPSEEK_API_KEY", "")
        )
        if not key:
            logger.warning("[hippocampus] no summary key; using extractive gist")
            return None
        return LLMSummarizer(
            api_key=key,
            base_url=str(
                config.get("summary_base", "https://api.deepseek.com/v1")
            ),
            model=str(config.get("summary_model", "deepseek-chat")),
        )

    def _self(self, scope: str) -> SelfModel:
        model = self._self_models.get(scope)
        if model is None:
            model = SelfModel(self.store, scope)
            self._self_models[scope] = model
        return model

    def _hippo(self, scope: str) -> Hippocampus:
        hippo = self._hippos.get(scope)
        if hippo is None:
            hippo = Hippocampus(
                scope=scope,
                store=self.store,
                embedder=self.embedder,
                summarizer=self.summarizer,
                persona=self.persona,
                async_index=True,
                async_summary=True,
            )
            self._hippos[scope] = hippo
        return hippo

    @staticmethod
    def _scope(event: AstrMessageEvent) -> str:
        return str(getattr(event, "unified_msg_origin", "") or "default")

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _message_id(self, event: AstrMessageEvent) -> str:
        return str(
            getattr(getattr(event, "message_obj", None), "message_id", "") or ""
        )

    def _mark(self, bucket: set, key: str) -> bool:
        if not key:
            return True
        if key in bucket:
            return False
        bucket.add(key)
        if len(bucket) > 800:
            for old in list(bucket)[:400]:
                bucket.discard(old)
        return True

    def _record(self, event: AstrMessageEvent, text: str, actor: str = "user") -> None:
        text = (text or "").strip()
        if not text:
            return
        key = self._message_id(event) + (":a" if actor == "assistant" else "")
        if not self._mark(self._recorded, key):
            return
        try:
            scope = self._scope(event)
            self._active_days.setdefault(scope, set()).add(
                datetime.now(timezone.utc).date()
            )
            self._hippo(scope).feed_episode(
                HEvent(
                    text=text,
                    actor=actor,
                    at=self._now(),
                    meta={
                        "sender_name": event.get_sender_name()
                        or event.get_sender_id()
                        or "你",
                        "refs": [self._message_id(event)],
                    },
                ),
                max_turns=10,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[hippocampus] record failed: %s", exc)

    # -- memory tools --------------------------------------------------------
    @filter.llm_tool(name="memory_search")
    async def memory_search(
        self, event: AstrMessageEvent, query: str, limit: int = 5
    ) -> str:
        """检索自己的长期记忆。只在需要回忆过去的事时调用。

        Args:
            query(string): 要查的关键词或一句话
            limit(number): 最多返回几条
        """
        scope = self._scope(event)
        hits = self._hippo(scope).recall(
            query, budget=max(1, min(10, int(limit))), reinforce=False
        )
        if not hits:
            return "没有找到相关记忆。"
        return "\n".join(
            "- [{}] L{} {}".format(hit.engram.engram_id, hit.level, hit.text)
            for hit in hits
        )

    @filter.llm_tool(name="memory_remember")
    async def memory_remember(
        self, event: AstrMessageEvent, text: str, importance: float = 0.6
    ) -> str:
        """把一件重要的事写进长期记忆。只在对方明确要求记住时调用。

        Args:
            text(string): 要记住的事，用第一人称
            importance(number): 重要度 0-1
        """
        if not text.strip():
            return "没有内容可记。"
        scope = self._scope(event)
        engram = self._hippo(scope).remember(
            HEvent(
                text=text.strip(),
                actor="user",
                at=self._now(),
                meta={"sender_name": event.get_sender_name() or "你"},
            ),
            importance=max(0.0, min(1.0, float(importance))),
        )
        return "记住了：{}".format(engram.engram_id)

    @filter.llm_tool(name="memory_profile")
    async def memory_profile(self, event: AstrMessageEvent) -> str:
        """查看你从相处中形成的性格、对对方的了解和你们之间的关系。"""
        return self._self(self._scope(event)).render() or "还没有形成稳定的自我认知。"

    # -- ambient group capture ----------------------------------------------
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """Record ambient group messages without waking the LLM.

        Addressed messages are recorded by on_llm_request; this listener only
        handles the chatter that never reaches the bot.
        """
        if not self.enabled or not self.capture_all_group:
            return
        if event.is_at_or_wake_command:
            return
        if event.get_sender_id() == event.get_self_id():
            return
        text = (event.message_str or "").strip()
        if not text or text.startswith("/"):
            return
        self._record(event, text)

    # -- wake on name -------------------------------------------------------
    @filter.regex(r".*(?:桃子姐姐|桃子姐).*")
    async def on_name_mention(self, event: AstrMessageEvent):
        """A group message that names her wakes the normal LLM path.

        No @ needed. The reply is probabilistic, so she feels like a person
        rather than an always-on keyword bot.
        """
        if not event.get_group_id():
            return
        if random.random() > max(0.0, min(1.0, self.name_reply_probability)):
            event.stop_event()
            return
        # A filter match alone only wakes the handler; the agent stage also
        # needs this flag, otherwise the message is silently dropped.
        event.is_wake = True
        event.is_at_or_wake_command = True

    # -- hooks ---------------------------------------------------------------
    @filter.on_llm_request()
    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        if not self.enabled:
            return
        is_group = bool(event.get_group_id())
        if is_group and not self.capture_group:
            return
        text = (event.message_str or "").strip()
        if not text:
            if not is_group:
                return
            # AstrBot's builtin empty-@ handler asks "what do you want?".
            # Replace that with a continuation of the recent topic.
            try:
                scope = self._scope(event)
                hippo = self._hippo(scope)
                recent = hippo.store.recent(scope, 3)
                snippets = []
                for engram in reversed(recent):
                    value = (engram.gist or engram.raw_text or "").strip()
                    if value:
                        snippets.append(value[:100])
                recent_lines = self._recent_user_lines(req)
                tail = "；".join(snippets[:2])
                req.prompt = (
                    "对方只 @ 了你，没有输入文字。不要问“有什么事”“想聊什么”。"
                    "优先接上他刚才说的最后几句话；不要跳到群里别的旧话题。"
                    + ("刚才他说：" + "；".join(recent_lines) if recent_lines else "")
                    + ("最近记忆：" + tail if tail else "")
                )
                self_block = self._self(scope).render()
                if self_block:
                    req.extra_user_content_parts.append(
                        TextPart(text=self_block).mark_as_temp()
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[hippocampus] empty mention failed: %s", exc)
            return
        recall_key = self._message_id(event)
        if not self._mark(self._recalled, recall_key):
            return
        self._record(event, text)

        if self._is_greeting(text):
            reply = self._greeting_reply(text)
            try:
                await event.send(MessageChain().message(reply))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[hippocampus] greeting send failed: %s", exc)
                return
            self._record(event, reply, actor="assistant")
            event.stop_event()
            return

        # Keep only memory tools. Other tools make the model talk like an agent.
        allowed = {"memory_search", "memory_remember", "memory_profile"}
        if req.func_tool is not None:
            req.func_tool.tools = [
                tool for tool in req.func_tool.tools if tool.name in allowed
            ]
            if not req.func_tool.tools:
                req.func_tool = None
        if req.system_prompt:
            for marker in ("When using tools:", "You MUST NOT return an empty response"):
                idx = req.system_prompt.find(marker)
                if idx >= 0:
                    req.system_prompt = req.system_prompt[:idx].rstrip()
        if req.func_tool is not None:
            req.system_prompt += (
                "\n你可以用 memory_search 主动查旧事，用 memory_remember 记重要的事，"
                "用 memory_profile 看自己已经形成的性格。只在需要时调用，调用时不要解释。\n"
            )

        scope = self._scope(event)
        try:
            hippo = self._hippo(scope)
            hits = (
                hippo.recall(
                    text, budget=self.recall_budget, max_chars=self.max_chars
                )
                if self.auto_recall
                else []
            )
        except Exception as exc:  # noqa: BLE001 - never break the chat
            logger.error("[hippocampus] recall failed: %s", exc)
            return

        if hits and self.auto_recall_cooldown_hours > 0:
            from datetime import timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(
                hours=self.auto_recall_cooldown_hours
            )
            hits = [
                hit
                for hit in hits
                if not hit.engram.last_mention_at
                or hit.engram.last_mention_at < cutoff
            ]
        if not hits:
            self_block = self._self(scope).render()
            if self_block:
                req.extra_user_content_parts.append(
                    TextPart(text=self_block).mark_as_temp()
                )
            return
        lines = []
        for hit in hits:
            prefix = "你隐约记得" if hit.level >= 2 else "你记得"
            lines.append("- {}：{}".format(prefix, hit.text))
        block = "<长期记忆>\n{}\n</长期记忆>".format("\n".join(lines))
        req.extra_user_content_parts.append(TextPart(text=block).mark_as_temp())
        try:
            hippo.mark_mentioned(
                [hit.engram.engram_id for hit in hits],
                now=datetime.now(timezone.utc),
            )
        except Exception:
            pass

        self_block = self._self(scope).render()
        if self_block:
            req.extra_user_content_parts.append(
                TextPart(text=self_block).mark_as_temp()
            )
        if self.debug:
            logger.info(
                "[hippocampus] injected %d memories for %s", len(hits), scope
            )

    @filter.on_llm_response()
    async def on_llm_response(
        self, event: AstrMessageEvent, resp: LLMResponse
    ) -> None:
        if not self.enabled:
            return
        text = (getattr(resp, "completion_text", "") or "").strip()
        if not text:
            return
        if self.typing_delay:
            await asyncio.sleep(random.uniform(0.3, 1.0))
        visible, extras = self._style_reply(text, event.message_str or "")
        try:
            resp.completion_text = visible
        except Exception:
            pass
        if extras:
            asyncio.create_task(self._send_extras(event, extras))
        self._record(
            event, " ".join([visible] + list(extras)), actor="assistant"
        )

    @staticmethod
    def _is_greeting(text: str) -> bool:
        cleaned = re.sub(r"[\s，。！？!?~～、,.]+", "", text or "")
        return cleaned in {
            "你好", "您好", "hi", "hello", "嗨", "哈喽",
            "在吗", "在不在", "在么", "在嘛",
            "早上好", "早安", "早", "晚上好", "晚安", "睡了",
            "谢谢", "多谢",
        }

    @staticmethod
    def _greeting_reply(text: str) -> str:
        cleaned = re.sub(r"[\s，。！？!?~～、,.]+", "", text or "")
        if cleaned in {"晚安", "睡了"}:
            return random.choice(["睡吧。", "晚安。"])
        if cleaned in {"谢谢", "多谢"}:
            return random.choice(["嗯。", "记着就行。"])
        if cleaned in {"早上好", "早安", "早"}:
            return "早。"
        if cleaned in {"在吗", "在不在", "在么", "在嘛"}:
            return random.choice(["在。咋了", "在呢，说。", "在，啥事。"])
        return random.choice(
            ["在。咋了", "哟，今天这么客气。", "在呢。", "嗯？说吧。", "咋了。"]
        )

    @staticmethod
    def _recent_user_lines(req: ProviderRequest, limit: int = 3):
        lines = []
        for message in reversed(getattr(req, "contexts", None) or []):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, list):
                text = " ".join(
                    str(x.get("text", ""))
                    for x in content
                    if isinstance(x, dict) and x.get("type") == "text"
                )
            else:
                text = str(content or "")
            text = re.sub(r"<system_reminder>.*?</system_reminder>", "", text, flags=re.S)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                lines.append(text[:80])
            if len(lines) >= limit:
                break
        return list(reversed(lines))

    def _style_reply(self, text: str, user_text: str = ""):
        """Return (first bubble, extra bubbles).

        Most replies are one short line; sometimes 2-3 bubbles; occasionally
        a longer paragraph. Styling is random so it does not feel scripted.
        """
        clean = re.sub(r"本鲸|本鱼|大肥鱼", "我", text or "")
        clean = re.sub(r"\s+", " ", clean).strip()
        for phrase in (
            "有什么可以帮",
            "希望能帮",
            "如果你愿意",
            "你想聊点",
            "你想试试",
            "你今天怎么突然",
            r"作为一?个?\s*(AI|人工智能|模型|助手)",
        ):
            clean = re.sub(phrase, "", clean)
        clean = clean.strip("，。；、 ")

        if "```" in clean or "http://" in clean or "https://" in clean:
            return clean, []

        if random.random() < self.essay_chance and len(clean) >= 50:
            return clean[: self.essay_max_chars], []

        parts = [p.strip() for p in re.split(r"(?<=[。！？!?])", clean) if p.strip()]
        if not parts:
            return clean[: self.max_reply_chars], []
        if len(parts) >= 2 and random.random() < self.multi_message_chance:
            chunks = []
            current = ""
            for part in parts:
                if current and len(current) + len(part) > 32:
                    chunks.append(current)
                    current = part
                else:
                    current = (current + part).strip()
            if current:
                chunks.append(current)
            chunks = chunks[:3]
            if len(chunks) >= 2:
                return chunks[0][: self.max_reply_chars], chunks[1:]

        visible = parts[0]
        if len(visible) < 8 and len(parts) > 1:
            visible += parts[1]
        if len(visible) > self.max_reply_chars:
            cut = visible[: self.max_reply_chars]
            for sep in ("，", ",", "。", "！", "？"):
                idx = cut.rfind(sep)
                if idx > 8:
                    cut = cut[:idx]
                    break
            visible = cut
        return visible.strip(), []

    async def _send_extras(self, event: AstrMessageEvent, extras) -> None:
        for extra in extras:
            await asyncio.sleep(random.uniform(0.6, 1.4))
            try:
                await event.send(MessageChain().message(extra))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[hippocampus] extra bubble failed: %s", exc)
                return

    # -- commands ------------------------------------------------------------
    @filter.command("hmem")
    async def hmem(self, event: AstrMessageEvent):
        """查看海马记忆状态。用法：/hmem [status|recall <query>|flush]"""
        scope = self._scope(event)
        parts = (event.message_str or "").split(maxsplit=2)
        action = parts[1].lower() if len(parts) > 1 else "status"
        hippo = self._hippo(scope)

        if action == "flush":
            hippo.flush_episode()
            yield event.plain_result("已把当前缓冲写入记忆。")
            return
        if action == "self":
            rendered = self._self(scope).render()
            yield event.plain_result(rendered or "还没有形成稳定的自我认知。")
            return
        if action == "reflect":
            ok = await self._reflect(scope, hippo)
            rendered = self._self(scope).render()
            yield event.plain_result(
                ("反思完成。\n" if ok else "暂时没有可反思的新记忆。\n") + rendered
            )
            return
        if action == "recall":
            query = parts[2] if len(parts) > 2 else ""
            if not query:
                yield event.plain_result("用法：/hmem recall <一句话>")
                return
            hits = hippo.recall(query, budget=self.recall_budget)
            if not hits:
                yield event.plain_result("没有召回任何记忆。")
                return
            lines = [
                "L{} {:.2f} {}".format(hit.level, hit.score, hit.text)
                for hit in hits
            ]
            yield event.plain_result("\n".join(lines))
            return

        stats = hippo.stats()
        yield event.plain_result(
            "scope: {}\nengrams: {}  facts: {}  edges: {}\nlevels: {}\n"
            "index_pending: {}  summary_pending: {}\n{}".format(
                scope,
                stats.get("total"),
                stats.get("facts"),
                stats.get("edges"),
                stats.get("levels"),
                stats.get("index_pending"),
                stats.get("summary_pending"),
                self.store.consistency_report(),
            )
        )

    async def _periodic_flush(self) -> None:
        try:
            while True:
                await asyncio.sleep(max(30, self.flush_interval))
                for scope, hippo in list(self._hippos.items()):
                    try:
                        hippo.flush_episode()
                        if self.auto_tick:
                            days = self._active_days.pop(scope, set())
                            hippo.tick(datetime.now(timezone.utc), active_days=days)
                            hippo.consolidate(datetime.now(timezone.utc))
                        await self._maybe_reflect(scope, hippo)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("[hippocampus] periodic flush failed: %s", exc)
        except asyncio.CancelledError:
            return

    # -- lifecycle -----------------------------------------------------------
    async def terminate(self) -> None:
        task = getattr(self, "_flush_task", None)
        if task is not None:
            task.cancel()
        for scope, hippo in list(self._hippos.items()):
            try:
                hippo.flush_episode()
                hippo.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[hippocampus] close %s failed: %s", scope, exc)
        self._hippos.clear()
        self.store.close()
        logger.info("[hippocampus] stopped")
