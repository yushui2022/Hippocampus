"""Companion brain service (protocol v1, port 6200).

The shell (Unity / browser) renders; this service owns memory, persona,
context assembly and the model call. It shares the same ``Brain`` design as
the QQ adapter, but a separate scope is fine: one character, one brain.

Run:

    python integrations/companion/server.py --env ../qq-stack/.env

Protocol: see integrations/companion/unity/README.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from aiohttp import ClientSession, WSMsgType, web

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hippocampus import Brain, LLMSummarizer, LocalEmbedder, Store, Utterance  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import webtools  # noqa: E402

CHARACTER_NAME = os.environ.get("CHARACTER_NAME", "桃子姐姐")
LOOK_MIN_INTERVAL = float(os.environ.get("LOOK_MIN_INTERVAL", "120"))
CHARACTER_ALIASES = os.environ.get("CHARACTER_ALIASES", "")

PERSONA = (
    f"你叫{CHARACTER_NAME}，一只蓝色小鲸鱼娘，住在小房间里。"
    + (f"别人也可能叫你{CHARACTER_ALIASES}，那都是在叫你。" if CHARACTER_ALIASES else "")
    + "有点小脾气，嘴硬但心软，贪吃、爱面子。"
    "不要自称本鲸、本鱼，不要提 AI、模型、助手。"
    "你住在一个小房间里，正在和面前的人聊天。"
)

STYLE = """
说话规则：
1. 大多数时候一两句，20 多个字；偶尔可以多说两句。
2. 不要每句都追问，不要列清单，不要说“有什么可以帮你”。
3. 一次最多两个气泡；气泡是连续说出来的，不是分段报告。
4. 被问是不是 AI：回“你猜”或“不是，别问”。
5. 你能上网看一眼：需要实时信息（新闻、天气、某个人、某个东西）时，把要查的内容填进 look 字段，系统会替你查好再让你回答；不需要就不要填，也不要编造。
6. 语气自然，像熟人聊天。可以嘴硬、可以吐槽，但不要骂人，不要用“杂鱼”“笨蛋”这类称呼。
"""

ACTIONS = {
    "walk_to": {"places", "objects", "user"},
    "run_to": {"places", "objects", "user"},
    "sit": {"objects"},
    "stand_up": set(),
    "jump": set(),
    "turn_to": {"objects", "user"},
    "look_at": {"objects", "user"},
    "wave": set(),
    "nod": set(),
    "shake_head": set(),
}

OUTPUT_RULES = """
输出必须是 JSON，不要 markdown：
{"say":["第一句","可选第二句"],"expression":"neutral|happy|sad|shy|angry|confused|smirk",
 "look":"要上网查的关键词或网址，没有就留空",
 "actions":[{"action":"动作名","target":"目标id或空"}]}
say 里每句都会作为一个气泡发出去，最多 2 句。
look 只在真的需要外部信息时填（新闻、天气、某个网站、某个不熟的东西）；
闲聊一律留空。填了之后你会先被允许上网看一眼，然后再回答。
expression 选最贴近当前情绪的一个：
happy=开心、angry=生气、sad=难过、shy=害羞、
confused=疑惑（没听懂、觉得对方说的离谱）、smirk=坏笑（准备损人、抓到你把柄）。
不确定就用 neutral，不要每句都带表情。
actions 可以为空；只允许 scene.actions 里出现的动作，只允许 scene 里的目标 id 或 user。
动作是点缀：大多数回复 actions 为空；偶尔点头、摇头、看她/他、跳一下或走到沙发，不要每句都动。
"""


def log(*args: Any) -> None:
    print("[companion {}]".format(time.strftime("%H:%M:%S")), *args, flush=True)


def load_env(path: Optional[str]) -> None:
    if not path:
        return
    file = Path(path)
    if not file.exists():
        return
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Companion:
    def __init__(self, db: str) -> None:
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        self.store = Store(db)
        summarizer = None
        if os.environ.get("DEEPSEEK_API_KEY"):
            summarizer = LLMSummarizer(
                api_key=os.environ["DEEPSEEK_API_KEY"],
                base_url=os.environ.get(
                    "CHAT_BASE", "https://api.deepseek.com/v1"
                ),
                model=os.environ.get("SUMMARY_MODEL", "deepseek-chat"),
            )
        embedder = None
        if os.environ.get("EMBED_MODEL"):
            embedder = LocalEmbedder(
                base_url=os.environ.get(
                    "EMBED_BASE", "http://127.0.0.1:11434/v1"
                ),
                model=os.environ["EMBED_MODEL"],
                dim=int(os.environ.get("EMBED_DIM", "1024")),
            )
        self.brain = Brain(
            scope=os.environ.get("BRAIN_SCOPE", "self:taozi"),
            store=self.store,
            summarizer=summarizer,
            embedder=embedder,
            persona=PERSONA,
        )
        self.session: Optional[ClientSession] = None
        self.model = os.environ.get("CHAT_MODEL", "deepseek-flash")
        self.base = os.environ.get("CHAT_BASE", "https://api.deepseek.com/v1")
        self.usage = {"date": "", "prompt": 0, "completion": 0, "total": 0}
        self._load_usage()
        self.api_key = os.environ.get("DEEPSEEK_API_KEY", "")

    async def http(self) -> ClientSession:
        if self.session is None or self.session.closed:
            self.session = ClientSession()
        return self.session

    async def close(self) -> None:
        self.brain.close()
        self.store.close()
        if self.session is not None:
            await self.session.close()

    # -- token 统计 -----------------------------------------------------------
    def _usage_path(self) -> Path:
        db = Path(os.environ.get("BRAIN_DB", "/data/brain.db"))
        return db.parent / "token_usage.json"

    def _load_usage(self) -> None:
        self.usage = {"date": "", "prompt": 0, "completion": 0}
        try:
            data = json.loads(self._usage_path().read_text())
            if data.get("date") == datetime.now().strftime("%Y-%m-%d"):
                self.usage.update(data)
        except Exception:  # noqa: BLE001
            pass

    def _add_usage(self, usage: dict) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self.usage.get("date") != today:
            self.usage = {"date": today, "prompt": 0, "completion": 0}
        self.usage["prompt"] += int(usage.get("prompt_tokens") or 0)
        self.usage["completion"] += int(usage.get("completion_tokens") or 0)
        self.usage["total"] = self.usage["prompt"] + self.usage["completion"]
        try:
            self._usage_path().write_text(json.dumps(self.usage, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass

    # -- model ---------------------------------------------------------------
    async def chat(self, messages: List[dict], *, json_mode: bool = True) -> str:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.9,
            "max_tokens": 600,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        session = await self.http()
        async with session.post(
            self.base.rstrip("/") + "/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
            json=payload,
            timeout=120,
        ) as resp:
            body = await resp.json()
        if resp.status >= 400:
            raise RuntimeError("chat {}: {}".format(resp.status, body))
        if body.get("usage"):
            self._add_usage(body["usage"])
        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError("no choices")
        return str(choices[0].get("message", {}).get("content") or "")

    # -- one turn ------------------------------------------------------------
    async def reply(
        self,
        session_state: dict,
        text: str,
        place: str = "living",
    ) -> dict:
        user_id = session_state.get("user_id") or "local"
        context_id = "local:dm:" + user_id
        now = datetime.now(timezone.utc)
        self.brain.observe(
            Utterance(
                text=text,
                context_id=context_id,
                context_kind="dyad",
                context_size=2,
                speaker_id=user_id,
                speaker_name=session_state.get("user_name") or "你",
                actor="user",
                addressed=True,
                witnesses=[user_id],
                at=now,
            )
        )

        bundle = self.brain.context(
            text, context_id=context_id, present=[user_id], budget=3, now=now
        )
        system_parts = [PERSONA, STYLE]
        rendered = bundle.render()
        if rendered:
            system_parts.append(rendered)
        self_block = self.brain.self_model().render(
            min_evidence=self.brain.profile.card_min_evidence
        )
        if self_block:
            system_parts.append(self_block)
        system_parts.append(
            "场景：\n"
            + json.dumps(session_state.get("scene") or {}, ensure_ascii=False)
        )
        system_parts.append(OUTPUT_RULES)

        history: Deque[dict] = session_state["history"]
        messages: List[dict] = [
            {"role": "system", "content": "\n\n".join(system_parts)}
        ]
        messages.extend(history)
        messages.append({"role": "user", "content": text})

        raw = ""
        try:
            raw = await self.chat(messages)
        except Exception as exc:  # noqa: BLE001
            log("model failed", repr(exc))
            return {
                "type": "turn",
                "turn_id": uuid.uuid4().hex[:8],
                "say": [{"text": "我这会儿有点卡，等下再说。", "emotion": "neutral"}],
                "expression": "neutral",
                "actions": [],
            }

        turn = self._parse_turn(raw, session_state)

        look = turn.get("look") or ""
        if look:
            now_ts = time.time()
            last_ts = float(session_state.get("last_look_at", 0))
            if now_ts - last_ts < LOOK_MIN_INTERVAL:
                log("look rate-limited", look[:40])
                look = ""
            material, site = await self._lookup(look)
            if not material:
                try:
                    raw2 = await self.chat(messages + [
                        {"role": "assistant", "content": raw[:500]},
                        {"role": "system", "content":
                            "你刚才想上网看一眼，但没查到有用的东西。"
                            "如实告诉用户没找到，用一到两句，不要编造内容，也不要假装查到了。"},
                    ])
                    turn = self._parse_turn(raw2, session_state)
                    raw = raw2
                except Exception as exc:  # noqa: BLE001
                    log("empty-lookup answer failed", repr(exc))
            if material:
                session_state["last_look_at"] = time.time()
                session_state["last_look_site"] = site or look
                follow = messages + [
                    {"role": "assistant", "content": raw[:500]},
                    {"role": "system", "content":
                        "你刚刚上网看了一眼，看到这些：\n" + material +
                        "\n\n现在用一到两句回应用户。像刚看完东西开口说话那样，"
                        "不要念资料，不要说“根据搜索结果”，不要提你查了资料。"},
                ]
                try:
                    raw2 = await self.chat(follow)
                    turn2 = self._parse_turn(raw2, session_state)
                    if (turn2.get("say") or [{}])[0].get("text", "").strip() not in ("", "嗯。"):
                        turn2["looking_at"] = site or look
                        turn = turn2
                        raw = raw2
                    else:
                        turn["looking_at"] = site or look
                except Exception as exc:  # noqa: BLE001
                    log("lookup answer failed", repr(exc))

        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": raw[:500]})

        spoken = " ".join(item["text"] for item in turn["say"])
        if spoken:
            self.brain.observe(
                Utterance(
                    text=spoken,
                    context_id=context_id,
                    context_kind="dyad",
                    context_size=2,
                    speaker_id="self",
                    speaker_name=CHARACTER_NAME,
                    actor="assistant",
                    addressed=False,
                    witnesses=[user_id],
                    at=datetime.now(timezone.utc),
                )
            )
            self.brain.mark_replied(context_id, user_id)
        return turn

    def _parse_turn(self, raw: str, session_state: dict) -> dict:
        text = (raw or "").strip()
        start, end = text.find("{"), text.rfind("}")
        data: Any = None
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                data = None

        if not isinstance(data, dict):
            return {
                "type": "turn",
                "turn_id": uuid.uuid4().hex[:8],
                "say": [{"text": text[:120] or "嗯。", "emotion": "neutral"}],
                "expression": "neutral",
                "actions": [],
            }

        say: List[dict] = []
        for item in (data.get("say") or [])[:2]:
            if isinstance(item, dict):
                line = str(item.get("text") or "").strip()
                emotion = str(item.get("emotion") or "neutral")
            else:
                line, emotion = str(item).strip(), "neutral"
            if line:
                say.append({"text": line[:120], "emotion": emotion})
        if not say:
            say = [{"text": "嗯。", "emotion": "neutral"}]

        actions = self._validate_actions(
            data.get("actions") or [], session_state.get("scene") or {}
        )
        return {
            "type": "turn",
            "turn_id": uuid.uuid4().hex[:8],
            "say": say,
            "expression": str(data.get("expression") or "neutral"),
            "actions": actions,
            "look": str(data.get("look") or "").strip()[:120],
        }

    JUNK_HOSTS = (
        "baike.baidu.com", "zhidao.baidu.com", "wenda.so.com", "baike.so.com",
        "dict.", "fanyi.", "youdao.com", "iciba.com",
    )
    JUNK_WORDS = ("广告", "下载", "登录", "注册", "优惠", "促销", "代理", "招商")
    TOPIC_POOL = (
        "零食 推荐", "猫 纸箱", "猫 品种", "深海鱼", "家常菜 做法",
        "动画 推荐", "下雨 原因", "旧手机 用途", "窗台 植物", "入睡 方法",
        "仓鼠 饲养", "拉面 做法", "多肉 浇水", "城市 夜景", "雨伞 修理",
        "草莓 保存", "咖啡 种类", "自行车 保养", "旅行 收拾行李", "面条 做法",
        "老照片 修复", "阳台 种菜", "手机 省电", "耳机 推荐", "云 形状",
        "花火 大会", "饼干 做法", "洗衣 技巧", "书桌 收纳", "热可可 做法",
    )

    async def _topics(self, session_state: dict) -> list[str]:
        pool = session_state.get("topics") or []
        if pool:
            return pool
        items = list(self.TOPIC_POOL)
        random.shuffle(items)
        session_state["topics"] = items
        return items

    async def _open_page(self, topic: str) -> dict:
        """搜一个话题，挑一条像样的结果，用搜索结果自带的摘要，不去抓正文。

        抓网页现在被百度/知乎 403、维基超时，搜索结果里的摘要反而更稳。
        """
        try:
            session = await self.http()
            results = await webtools.search(session, topic, limit=6)
            words = [w for w in re.split(r"[\s,，]+", topic) if w]
            best = None
            best_score = 0
            for r in results:
                url = r.get("url") or ""
                host = webtools.site_of(url)
                if any(b in host for b in self.JUNK_HOSTS):
                    continue
                title = (r.get("title") or "").strip()
                snippet = (r.get("snippet") or "").strip()
                if len(snippet) < 30 or any(w in title for w in self.JUNK_WORDS):
                    continue
                hay = title + snippet
                score = sum(1 for w in words if w in hay)
                if score > best_score:
                    best_score = score
                    best = {
                        "ok": True,
                        "topic": topic,
                        "site": host,
                        "url": url,
                        "title": title[:70],
                        "summary": snippet[:110],
                        "text": snippet[:600],
                    }
            if best and best_score >= 1:
                return best
        except Exception as exc:  # noqa: BLE001
            log("open page failed", repr(exc))
        return {"ok": False, "topic": topic}

    async def browse_tick(self, session_state: dict) -> dict:
        """开一个新页面；不好看就换，最多试三个话题。"""
        pool = await self._topics(session_state)
        for _ in range(3):
            if not pool:
                pool = list(self.TOPIC_POOL)
            topic = pool.pop(0)
            session_state["topics"] = pool
            page = await self._open_page(topic)
            if page.get("ok"):
                session_state["current_page"] = page
                session_state["pages_opened"] = int(session_state.get("pages_opened", 0)) + 1
                return page
        session_state["current_page"] = {}
        return {"ok": False}

    async def browse_comment(self, session_state: dict) -> dict:
        """对当前页面嘟囔一句。"""
        page = session_state.get("current_page") or {}
        if not page.get("ok"):
            return {}
        try:
            raw = await self.chat([
                {"role": "system", "content": PERSONA + "\n" + STYLE},
                {"role": "system", "content":
                    "你正在看这个网页：\n标题：" + page.get("title", "") +
                    "\n内容：" + page.get("text", "")[:800] +
                    '\n\n用一句话嘟囔一下，像自言自语，不要提问。只回 JSON：{"say":["一句话"]}'},
            ])
        except Exception as exc:  # noqa: BLE001
            log("browse comment failed", repr(exc))
            return {}
        turn = self._parse_turn(raw, session_state)
        text = (turn.get("say") or [{}])[0].get("text", "").strip()
        if not text or text == "嗯。":
            return {}
        return {
            "type": "turn",
            "turn_id": uuid.uuid4().hex[:8],
            "say": turn.get("say"),
            "expression": turn.get("expression", "neutral"),
            "actions": [],
            "looking_at": page.get("site", ""),
            "autonomous": True,
        }

    async def _lookup(self, query: str) -> tuple[str, str]:
        """Search or fetch, return (material for the model, site label for the UI)."""
        try:
            session = await self.http()
            if query.startswith("http://") or query.startswith("https://"):
                page = await webtools.fetch_page(session, query)
                if not page:
                    return "", ""
                return webtools.brief([], [page]), page.get("site") or "网页"
            results = await webtools.search(session, query, limit=4)
            if not results:
                return "", ""
            pages = []
            top = results[0]["url"]
            if top.startswith("http"):
                page = await webtools.fetch_page(session, top)
                if page:
                    pages.append(page)
            label = webtools.site_of(top) or "网页"
            return webtools.brief(results, pages), label
        except Exception as exc:  # noqa: BLE001
            log("lookup failed", repr(exc))
            return "", ""

    def _validate_actions(self, actions: Any, scene: dict) -> List[dict]:
        if not isinstance(actions, list):
            return []
        places = {
            str(item.get("id"))
            for item in scene.get("places") or []
            if isinstance(item, dict)
        }
        objects = {
            str(item.get("id")): item
            for item in scene.get("objects") or []
            if isinstance(item, dict) and item.get("id")
        }
        valid = []
        for action in actions[:3]:
            if not isinstance(action, dict):
                continue
            verb = str(action.get("action") or action.get("do") or "")
            target = str(action.get("target") or "")
            if verb not in ACTIONS:
                continue
            kinds = ACTIONS[verb]
            if kinds and target not in places | set(objects) | {"user"}:
                continue
            if verb == "sit":
                item = objects.get(target) or {}
                if "sit" not in (item.get("affords") or []):
                    continue
            valid.append(
                {
                    "id": uuid.uuid4().hex[:6],
                    "do": verb,
                    "action": verb,
                    "target": target,
                }
            )
        return valid

    # -- background ----------------------------------------------------------
    async def maintain(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                report = self.brain.maintain()
                if report.get("promoted") or report.get("dropped"):
                    log(
                        "maintain",
                        "promoted={}".format(report.get("promoted")),
                        "dropped={}".format(report.get("dropped")),
                    )
            except Exception as exc:  # noqa: BLE001
                log("maintain failed", repr(exc))


def create_app(companion: Companion) -> web.Application:
    app = web.Application()
    app["companion"] = companion
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/health", health)
    app.on_startup.append(start_background)
    app.on_cleanup.append(stop_background)
    return app


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    companion: Companion = request.app["companion"]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    state = {
        "user_id": "local",
        "user_name": "你",
        "scene": {},
        "history": deque(maxlen=12),
    }
    log("shell connected")
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            kind = data.get("type")
            if kind == "hello":
                state["user_id"] = str(data.get("user_id") or "local")
                state["user_name"] = str(data.get("user_name") or "你")
                state["scene"] = data.get("scene") or {}
                await ws.send_json(
                    {
                        "type": "welcome",
                        "character": CHARACTER_NAME,
                        "voice": {"format": "pcm", "sample_rate": 24000},
                    }
                )
            elif kind == "browse_tick":
                page = await companion.browse_tick(state)
                await ws.send_json({"type": "browsing", **page})
            elif kind == "browse_comment":
                turn = await companion.browse_comment(state)
                if turn:
                    await ws.send_json(turn)
                    log("browse turn", " | ".join(x.get("text", "") for x in turn["say"]))
            elif kind == "user_text":
                text = str(data.get("text") or "").strip()
                if not text:
                    continue
                log("user", text[:40])
                turn = await companion.reply(
                    state, text, str(data.get("place") or "living")
                )
                await ws.send_json(turn)
                log("turn", " | ".join(item["text"] for item in turn["say"]))
    finally:
        log("shell disconnected")
    return ws


async def health(request: web.Request) -> web.Response:
    companion: Companion = request.app["companion"]
    stats = companion.brain.stats()
    return web.json_response(
        {
            "ok": True,
            "tokens": companion.usage,
            "scope": companion.brain.scope,
            "engrams": stats.get("total"),
            "buffer": stats.get("buffer_pending"),
            "entities": stats.get("entities"),
            "model": companion.model,
        }
    )


async def start_background(app: web.Application) -> None:
    app["task"] = asyncio.create_task(app["companion"].maintain())


async def stop_background(app: web.Application) -> None:
    task = app.get("task")
    if task:
        task.cancel()
    await app["companion"].close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=os.environ.get("COMPANION_ENV", ""))
    parser.add_argument("--host", default=os.environ.get("COMPANION_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("COMPANION_PORT", "6200")))
    parser.add_argument(
        "--db",
        default=os.environ.get("COMPANION_DB", "data/companion/brain.db"),
    )
    args = parser.parse_args()
    load_env(args.env)
    companion = Companion(args.db)
    log("listening on {}:{} db={} model={}".format(args.host, args.port, args.db, companion.model))
    web.run_app(create_app(companion), host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
