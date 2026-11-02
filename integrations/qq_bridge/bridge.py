"""Route B: NapCat OneBot transport + Hippocampus-owned context.

AstrBot is no longer in the reply path. This process owns:
  - conversation history per QQ scope
  - memory write/recall/tick/reflect
  - persona/self-model/prompt assembly
  - reply rhythm (single / multi-bubble / essay)

NapCat connects to this process as a reverse WebSocket client.
"""

from __future__ import annotations

import asyncio
import base64
import html
import ipaddress
import json
import os
import random
import re
import socket
import sys
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import ClientSession, web

HIPPO_PATH = os.environ.get("HIPPOCAMPUS_PATH", "/opt/hippocampus")
if Path(HIPPO_PATH).exists() and HIPPO_PATH not in sys.path:
    sys.path.insert(0, HIPPO_PATH)

from hippocampus import Event, Hippocampus, LLMSummarizer, LocalEmbedder, Store
from hippocampus.self_model import SelfModel, parse_reflection, reflection_prompt

PORT = int(os.environ.get("BRIDGE_PORT", "6199"))
MODEL = os.environ.get("CHAT_MODEL", "deepseek-flash")
API_BASE = os.environ.get("CHAT_BASE", "https://api.deepseek.com/v1")
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
MEMORY_DB = os.environ.get(
    "MEMORY_DB", "/data/hippocampus/memory.db"
)
EMBED_BASE = os.environ.get(
    "EMBED_BASE", "http://host.docker.internal:11434/v1"
)
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
BOT_NAMES = [x for x in os.environ.get("BOT_NAMES", "桃子姐姐,桃子姐").split(",") if x]
TEST_GROUP = os.environ.get("TEST_GROUP", "922410298")
CAPTURE_GROUPS = {
    x.strip() for x in os.environ.get("CAPTURE_GROUPS", TEST_GROUP).split(",") if x.strip()
}
ACTIVE_CHANCE = float(os.environ.get("ACTIVE_CHANCE", "0.25"))
NAME_CHANCE = float(os.environ.get("NAME_CHANCE", "0.95"))
MAX_HISTORY = int(os.environ.get("MAX_HISTORY", "14"))
RECALL_BUDGET = int(os.environ.get("RECALL_BUDGET", "2"))
WEB_TOOLS = os.environ.get("WEB_TOOLS", "true").lower() != "false"

PERSONA = os.environ.get(
    "PERSONA",
    "你叫桃子姐姐。不要自称本鲸、本鱼、大肥鱼，不要提 AI、模型、助手。"
    "你在 QQ 上和大家闲聊，不是在完成任务。",
)
STYLE = (
    "\n说话规则：\n"
    "1. 大多数时候一两句就够，20 个字左右；偶尔可以多说两句。\n"
    "2. 回答完可以停，也可以偶尔接一句短的；不要每句都追问。\n"
    "3. 不要用“呀、呢、哦、～”堆可爱，最多一个语气词。\n"
    "4. 被问是不是 AI：回“你猜”或“不是，别问”。\n"
    "5. 不要只回一个字，至少让对方能接话；但别写小作文。\n"
    "6. 不列清单，不说“有什么可以帮你”。\n"
    "7. 群聊里绝不透露私聊内容。\n"
    "8. 需要实时信息/新闻/链接时，调用 web_search 或 web_fetch；不要凭记忆编造最新信息。\n"
)


def now() -> datetime:
    return datetime.now(timezone.utc)


class OneBot:
    def __init__(self) -> None:
        self.ws: Optional[web.WebSocketResponse] = None
        self.pending: Dict[str, asyncio.Future] = {}

    async def send_action(self, action: str, params: dict, timeout: float = 20.0):
        if self.ws is None or self.ws.closed:
            raise RuntimeError("napcat websocket is not connected")
        echo = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.pending[echo] = fut
        await self.ws.send_str(
            json.dumps({"action": action, "params": params, "echo": echo})
        )
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(echo, None)

    async def send_text(self, *, group_id: str = "", user_id: str = "", text: str):
        params = {"message": [{"type": "text", "data": {"text": text}}]}
        if group_id:
            params["group_id"] = int(group_id)
            return await self.send_action("send_group_msg", params)
        params["user_id"] = int(user_id)
        return await self.send_action("send_private_msg", params)


class Bridge:
    def __init__(self) -> None:
        self.bot = OneBot()
        self.store = Store(MEMORY_DB)
        self.embedder = LocalEmbedder(base_url=EMBED_BASE, model=EMBED_MODEL)
        self.summarizer = (
            LLMSummarizer(
                api_key=API_KEY,
                base_url=API_BASE,
                model=os.environ.get("SUMMARY_MODEL", "deepseek-chat"),
            )
            if API_KEY
            else None
        )
        self.hippos: Dict[str, Hippocampus] = {}
        self.self_models: Dict[str, SelfModel] = {}
        self.histories: Dict[str, deque] = defaultdict(lambda: deque(maxlen=MAX_HISTORY))
        self.active_days: Dict[str, set] = defaultdict(set)
        self.seen: set = set()
        self.group_names: Dict[str, str] = {}
        self._session: Optional[ClientSession] = None

    # -- infra ---------------------------------------------------------------
    def hippo(self, scope: str) -> Hippocampus:
        item = self.hippos.get(scope)
        if item is None:
            item = Hippocampus(
                scope=scope,
                store=self.store,
                embedder=self.embedder,
                summarizer=self.summarizer,
                persona=PERSONA,
                async_index=True,
                async_summary=True,
            )
            self.hippos[scope] = item
        return item

    def self_model(self, scope: str) -> SelfModel:
        item = self.self_models.get(scope)
        if item is None:
            item = SelfModel(self.store, scope)
            self.self_models[scope] = item
        return item

    @property
    def session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession()
        return self._session

    # -- onebot --------------------------------------------------------------
    async def on_message(self, data: dict) -> None:
        if data.get("post_type") != "message":
            return
        msg_type = data.get("message_type")
        self_id = str(data.get("self_id", ""))
        user_id = str(data.get("user_id", ""))
        if user_id == self_id:
            return
        group_id = str(data.get("group_id", "")) if msg_type == "group" else ""
        message_id = str(data.get("message_id", ""))
        if message_id and message_id in self.seen:
            return
        if message_id:
            self.seen.add(message_id)
            if len(self.seen) > 1000:
                self.seen = set(list(self.seen)[-500:])

        sender = data.get("sender") or {}
        name = str(sender.get("card") or sender.get("nickname") or user_id)
        text_parts: List[str] = []
        images: List[str] = []
        at_self = False
        for seg in data.get("message") or []:
            kind = seg.get("type")
            payload = seg.get("data") or {}
            if kind == "text":
                text_parts.append(str(payload.get("text", "")))
            elif kind == "at" and str(payload.get("qq", "")) == self_id:
                at_self = True
            elif kind == "image":
                url = payload.get("url") or payload.get("file") or ""
                if url:
                    images.append(str(url))
        text = "".join(text_parts).strip()

        scope = (
            "qq:GroupMessage:" + group_id
            if group_id
            else "qq:FriendMessage:" + user_id
        )
        self.active_days[scope].add(now().date())
        if text.startswith("/hmem"):
            await self.command(scope, text, group_id, user_id)
            return

        is_group = bool(group_id)
        named = any(n in text for n in BOT_NAMES)
        mentioned = (not is_group) or at_self or named
        if is_group and not mentioned and text:
            # Probabilistic interjection only in the designated test group.
            if group_id == TEST_GROUP and random.random() < ACTIVE_CHANCE:
                mentioned = True

        # Record into long-term memory only for private chats, the test group,
        # or messages that actually address the bot. Other group chatter stays
        # in short-term history but does not pollute the memory DB.
        should_capture = (not is_group) or group_id in CAPTURE_GROUPS or mentioned
        record_text = text or ("[图片]" if images else "")
        if record_text:
            self.histories[scope].append(
                {"role": "user", "name": name, "text": record_text}
            )
            if should_capture:
                try:
                    self.hippo(scope).feed_episode(
                        Event(
                            text=record_text,
                            actor="user",
                            at=now(),
                            meta={"sender_name": name, "refs": [message_id]},
                        ),
                        max_turns=10,
                    )
                except Exception as exc:  # noqa: BLE001
                    log("memory write failed", exc)

        log("recv", scope, name, (text or "[图片]")[:60])
        if not mentioned:
            return
        if mentioned and text and not at_self and any(n in text for n in BOT_NAMES):
            if random.random() > NAME_CHANCE:
                return

        await self.reply(
            scope=scope,
            text=text,
            name=name,
            group_id=group_id,
            user_id=user_id,
            images=images,
            empty_mention=bool(is_group and at_self and not text),
        )

    async def command(self, scope: str, text: str, group_id: str, user_id: str) -> None:
        parts = text.split(maxsplit=2)
        action = parts[1].lower() if len(parts) > 1 else "status"
        hippo = self.hippo(scope)
        if action == "flush":
            hippo.flush_episode()
            await self.bot.send_text(group_id=group_id, user_id=user_id, text="已写入记忆。")
            return
        if action == "recall":
            query = parts[2] if len(parts) > 2 else ""
            if not query:
                await self.bot.send_text(group_id=group_id, user_id=user_id, text="用法：/hmem recall 关键词")
                return
            hits = hippo.recall(query, budget=6)
            body = "\n".join(
                "L{} {:.2f} {}".format(h.level, h.score, h.text) for h in hits
            ) or "没有找到记忆。"
            await self.bot.send_text(group_id=group_id, user_id=user_id, text=body)
            return
        if action == "self":
            body = self.self_model(scope).render() or "还没有形成稳定的自我认知。"
            await self.bot.send_text(group_id=group_id, user_id=user_id, text=body)
            return
        if action == "reflect":
            await self.reflect(scope, hippo, force=True)
            body = self.self_model(scope).render() or "还没有形成稳定的自我认知。"
            await self.bot.send_text(group_id=group_id, user_id=user_id, text=body)
            return
        stats = hippo.stats()
        body = (
            "记忆 {} 条，事实 {} 条，等级 {}\n{}"
            .format(
                stats.get("total"),
                stats.get("facts"),
                stats.get("levels"),
                self.self_model(scope).render() or "（还没有自我认知）",
            )
        )
        await self.bot.send_text(group_id=group_id, user_id=user_id, text=body)

    async def reply(
        self,
        *,
        scope: str,
        text: str,
        name: str,
        group_id: str,
        user_id: str,
        images: List[str],
        empty_mention: bool,
    ) -> None:
        if not text and not images and not empty_mention:
            return
        log("reply begin", scope, (text or "[image]")[:50])
        if self._is_greeting(text):
            answer = self._greeting_reply(text)
            await self.bot.send_text(group_id=group_id, user_id=user_id, text=answer)
            self.hippo(scope).feed_episode(
                Event(answer, actor="assistant", at=now(), meta={"refs": []})
            )
            self.histories[scope].append({"role": "assistant", "text": answer})
            return

        query = text
        if empty_mention:
            recent = [h["text"] for h in list(self.histories[scope])[-4:] if h.get("role") == "user"]
            query = " ".join(recent[-3:]) or "最近"
        log("recall begin", scope, query[:40])
        try:
            hits = self.hippo(scope).recall(query, budget=RECALL_BUDGET)
        except Exception as exc:  # noqa: BLE001
            log("recall failed", exc)
            hits = []
        log("recall done", scope, len(hits))
        memory_block = ""
        if hits:
            memory_block = "<长期记忆>\n" + "\n".join(
                "- {}".format(h.text) for h in hits
            ) + "\n</长期记忆>\n"

        system = PERSONA + STYLE + "\n" + self.self_model(scope).render() + "\n" + memory_block
        if empty_mention:
            system += (
                "\n对方只 @ 了你，没有输入文字。不要问“有什么事/想聊什么”，"
                "优先接上他刚才说的话；不要跳到群里别的旧话题。\n"
            )

        messages: List[dict] = [{"role": "system", "content": system}]
        history = list(self.histories[scope])[-8:]
        for item in history:
            if item.get("text") == text and item.get("role") == "user":
                continue
            prefix = "{}: ".format(item.get("name", "")) if item.get("role") == "user" else ""
            messages.append({"role": item["role"], "content": prefix + item["text"]})

        current_content: Any = text
        if images:
            parts: List[dict] = [{"type": "text", "text": text or "（发了一张图片）"}]
            for url in images[:3]:
                data_url = await self._image_data(url)
                if data_url:
                    parts.append({"type": "image_url", "image_url": {"url": data_url}})
            current_content = parts
        messages.append({"role": "user", "content": current_content})

        log("chat begin", scope, len(text))
        try:
            answer = await self._chat(messages)
        except Exception as exc:  # noqa: BLE001
            log("chat failed", exc)
            return
        log("chat done", scope, len(answer or ""))
        answer = self._style(answer, text)
        if not answer:
            return

        # Human-ish reply rhythm.
        visible, extras = self._split(answer)
        if visible:
            await asyncio.sleep(random.uniform(0.3, 1.0))
            await self.bot.send_text(group_id=group_id, user_id=user_id, text=visible)
        for extra in extras:
            await asyncio.sleep(random.uniform(0.6, 1.4))
            await self.bot.send_text(group_id=group_id, user_id=user_id, text=extra)

        full = " ".join([visible] + extras)
        log("reply", scope, full[:100])
        try:
            self.hippo(scope).feed_episode(
                Event(full, actor="assistant", at=now(), meta={"refs": []})
            )
        except Exception as exc:  # noqa: BLE001
            log("assistant memory write failed", exc)
        self.histories[scope].append({"role": "assistant", "text": full})

    def _tools(self) -> List[dict]:
        if not WEB_TOOLS:
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "搜索实时网页信息。需要新闻、天气、链接、最新事实时使用。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索词"},
                            "limit": {"type": "integer", "description": "最多几条，默认 5"},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_fetch",
                    "description": "打开一个 http/https 链接并读出正文。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "完整链接"}
                        },
                        "required": ["url"],
                    },
                },
            },
        ]

    async def _chat(self, messages: List[dict]) -> str:
        tools = self._tools()
        for _round in range(3):
            payload = {
                "model": MODEL,
                "messages": messages,
                "temperature": 0.9,
                "max_tokens": 900,
                "stream": False,
            }
            if tools:
                payload["tools"] = tools
            async with self.session.post(
                API_BASE.rstrip("/") + "/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + API_KEY,
                },
                json=payload,
                timeout=120,
            ) as resp:
                body = await resp.json()
            if resp.status >= 400:
                raise RuntimeError("deepseek error {}: {}".format(resp.status, body))
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
                result = await self._run_tool(name, args)
                log("tool", name, str(args)[:80], "->", len(result), "chars")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": result[:6000],
                    }
                )
        return ""

    async def _run_tool(self, name: str, args: dict) -> str:
        try:
            if name == "web_search":
                return await self.web_search(
                    str(args.get("query") or ""), int(args.get("limit") or 5)
                )
            if name == "web_fetch":
                return await self.web_fetch(str(args.get("url") or ""))
            return "未知工具：{}".format(name)
        except Exception as exc:  # noqa: BLE001
            log("tool failed", name, exc)
            return "工具执行失败：{}".format(exc)

    async def _get_text(self, url: str, limit: int = 300000) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            )
        }
        async with self.session.get(url, headers=headers, timeout=20) as resp:
            if resp.status >= 400:
                raise RuntimeError("HTTP {}".format(resp.status))
            data = await resp.read()
        return data[:limit].decode("utf-8", "ignore")

    @staticmethod
    def _plain(text: str) -> str:
        text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _safe_url(url: str) -> bool:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        host = parsed.hostname.lower()
        if host in ("localhost", "host.docker.internal"):
            return False
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_unspecified
            ):
                return False
        return True

    async def web_search(self, query: str, limit: int = 5) -> str:
        from urllib.parse import quote_plus

        if not query.strip():
            return "搜索词为空。"
        limit = max(1, min(8, int(limit)))
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        }
        url = "https://www.bing.com/search?q={}&setlang=zh-Hans&cc=cn".format(
            quote_plus(query)
        )
        data = ""
        try:
            async with self.session.get(url, headers=headers, timeout=20) as resp:
                if resp.status < 400:
                    data = (await resp.read())[:400000].decode("utf-8", "ignore")
        except Exception as exc:  # noqa: BLE001
            log("bing failed", exc)

        results = []
        for block in re.findall(r'<li class="b_algo".*?</li>', data, re.S | re.I):
            m = re.search(
                r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                block,
                re.S | re.I,
            )
            if not m:
                continue
            href = html.unescape(m.group(1))
            title = self._plain(m.group(2))
            sm = re.search(r"<p[^>]*>(.*?)</p>", block, re.S | re.I)
            snippet = self._plain(sm.group(1)) if sm else ""
            if href and title:
                results.append((title, href, snippet))
            if len(results) >= limit:
                break

        if not results:
            # lite.duckduckgo.com fallback
            try:
                ddg = "https://lite.duckduckgo.com/lite/?q=" + quote_plus(query)
                async with self.session.get(ddg, headers=headers, timeout=20) as resp:
                    data = (await resp.read())[:300000].decode("utf-8", "ignore")
                links = re.findall(
                    r'<a[^>]*class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                    data,
                    re.S | re.I,
                )
                snippets = re.findall(
                    r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
                    data,
                    re.S | re.I,
                )
                for i, (href, title) in enumerate(links[:limit]):
                    snippet = self._plain(snippets[i]) if i < len(snippets) else ""
                    results.append(
                        (self._plain(title), html.unescape(href), snippet)
                    )
            except Exception as exc:  # noqa: BLE001
                log("ddg failed", exc)

        if not results:
            return "没有找到搜索结果。"
        lines = []
        for i, (title, href, snippet) in enumerate(results, 1):
            lines.append("{}. {}\n{}\n{}".format(i, title, href, snippet[:200]))
        return "\n\n".join(lines)

    async def web_fetch(self, url: str) -> str:
        if not self._safe_url(url):
            return "这个地址不允许抓取。"
        data = await self._get_text(url, limit=800000)
        text = self._plain(data)
        return text[:4000] if text else "页面没有可读正文。"

    async def _image_data(self, url: str) -> str:
        try:
            if url.startswith("base64://"):
                return "data:image/png;base64," + url[len("base64://") :]
            if url.startswith("http://") or url.startswith("https://"):
                async with self.session.get(url, timeout=30) as resp:
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
        except Exception as exc:  # noqa: BLE001
            log("image fetch failed", exc)
            return ""

    # -- style ---------------------------------------------------------------
    @staticmethod
    def _is_greeting(text: str) -> bool:
        cleaned = re.sub(r"[\s，。！？!?~～、,.]+", "", text or "")
        return cleaned in {
            "你好", "您好", "hi", "hello", "嗨", "哈喽",
            "在吗", "在不在", "在么", "在嘛", "早上好", "早安", "早",
            "晚上好", "晚安", "睡了", "谢谢", "多谢",
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
        return random.choice(["在。咋了", "哟，今天这么客气。", "在呢。", "嗯？说吧。"])

    def _style(self, text: str, user_text: str = "") -> str:
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
        if any(w in (user_text or "").lower() for w in ("ai", "模型", "机器人")) and (
            "桃子姐姐" in clean or "模型" in clean or "AI" in clean
        ):
            return random.choice(["你猜。", "不是，别问。", "问这个干嘛。"])
        return clean

    def _split(self, text: str):
        if len(text) >= 50 and random.random() < 0.12:
            return text[:220], []
        parts = [p.strip() for p in re.split(r"(?<=[。！？!?])", text) if p.strip()]
        if not parts:
            return text[:60], []
        if len(parts) >= 2 and random.random() < 0.4:
            chunks = []
            cur = ""
            for part in parts:
                if cur and len(cur) + len(part) > 32:
                    chunks.append(cur)
                    cur = part
                else:
                    cur = (cur + part).strip()
            if cur:
                chunks.append(cur)
            chunks = chunks[:3]
            if len(chunks) >= 2:
                return chunks[0][:60], chunks[1:]
        out = parts[0]
        if len(out) < 8 and len(parts) > 1:
            out += parts[1]
        if len(out) > 60:
            out = out[:60]
        return out, []

    # -- maintenance ---------------------------------------------------------
    async def maintenance(self) -> None:
        while True:
            await asyncio.sleep(60)
            for scope, hippo in list(self.hippos.items()):
                try:
                    hippo.flush_episode()
                    days = self.active_days.pop(scope, set())
                    hippo.tick(now(), active_days=days)
                    hippo.consolidate(now())
                    await self.reflect(scope, hippo)
                except Exception as exc:  # noqa: BLE001
                    log("maintenance failed", exc)

    async def reflect(self, scope: str, hippo: Hippocampus, force: bool = False) -> None:
        if self.summarizer is None:
            return
        model = self.self_model(scope)
        pending = model.pending(hippo.store.list(scope))
        if not force and len(pending) < 4:
            return
        if not pending:
            return
        try:
            raw = await asyncio.to_thread(
                self.summarizer.chat, reflection_prompt(model, pending)
            )
            model.merge(parse_reflection(raw))
            model.mark_reflected(pending)
            log("reflected", scope, len(pending))
        except Exception as exc:  # noqa: BLE001
            log("reflection failed", exc)


def log(*args) -> None:
    stamp = time.strftime("%H:%M:%S")
    print("[bridge {}]".format(stamp), *args, flush=True)


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    bridge: Bridge = request.app["bridge"]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    bridge.bot.ws = ws
    log("napcat connected")
    try:
        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            echo = data.get("echo")
            if echo and echo in bridge.bot.pending:
                fut = bridge.bot.pending[echo]
                if not fut.done():
                    fut.set_result(data)
                continue
            asyncio.create_task(bridge.on_message(data))
    finally:
        if bridge.bot.ws is ws:
            bridge.bot.ws = None
        log("napcat disconnected")
    return ws


async def main() -> None:
    Path(MEMORY_DB).parent.mkdir(parents=True, exist_ok=True)
    bridge = Bridge()
    app = web.Application()
    app["bridge"] = bridge
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/health", lambda r: web.json_response({"ok": True}))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log("listening on", PORT, "model", MODEL, "db", MEMORY_DB)
    task = asyncio.create_task(bridge.maintenance())
    try:
        await asyncio.Event().wait()
    finally:
        task.cancel()
        for hippo in bridge.hippos.values():
            try:
                hippo.flush_episode()
                hippo.close()
            except Exception:
                pass
        bridge.store.close()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
