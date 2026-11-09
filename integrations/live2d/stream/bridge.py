"""Danmaku bridge: comments in -> Brain reply -> stage page.

Runs one persistent WebSocket to the companion brain (6200) and exposes:

    POST /comment  {"user": "小明", "text": "你好呀"}   accept a comment
    POST /say      {"text": "..."}                      alias, no user name
    GET  /ws                                            stage page socket
    GET  /health                                        queue + brain state

The stage page never talks to the brain directly: the bridge owns the single
conversation so replies stay in order and can be rate limited.

    python bridge.py            # http://127.0.0.1:6220/stream.html
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import time
from collections import deque
from typing import Any

import aiohttp
from aiohttp import WSMsgType, web

BRAIN_WS = os.environ.get("BRAIN_WS", "ws://127.0.0.1:6200/ws")
PORT = int(os.environ.get("STREAM_PORT", "6220"))
MIN_GAP = float(os.environ.get("STREAM_MIN_GAP", "1.2"))      # seconds between replies
MAX_LEN = int(os.environ.get("STREAM_MAX_LEN", "120"))
DEDUPE_WINDOW = float(os.environ.get("STREAM_DEDUPE", "30"))
TURN_TIMEOUT = float(os.environ.get("STREAM_TURN_TIMEOUT", "45"))
QUEUE_MAX = int(os.environ.get("STREAM_QUEUE_MAX", "12"))
# 自动冲浪：安静多久她自己去上网，以及两次之间至少隔多久
BROWSE_TICK = float(os.environ.get("STREAM_BROWSE_TICK", "15"))
BROWSE_QUIET = float(os.environ.get("STREAM_BROWSE_QUIET", "20"))
BROWSE_COMMENT_EVERY = int(os.environ.get("STREAM_BROWSE_COMMENT_EVERY", "4"))

STATS_PATH = Path(__file__).resolve().parent / ".stats.json"

URL_RE = re.compile(r"https?://\S+|www\.\S+")
CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def log(*args: Any) -> None:
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def clean(text: str) -> str:
    text = CTRL_RE.sub("", str(text or ""))
    text = URL_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_LEN]


def load_stats() -> dict:
    today = time.strftime("%Y-%m-%d")
    try:
        data = json.loads(STATS_PATH.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        data = {}
    if data.get("date") != today:
        data = {"date": today, "comments": 0, "gifts": 0, "users": {}, "gifters": {}}
    data.setdefault("users", {})
    data.setdefault("gifters", {})
    return data


def save_stats(stats: dict) -> None:
    try:
        STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False), "utf-8")
    except Exception:  # noqa: BLE001
        pass


class Bridge:
    def __init__(self) -> None:
        self.queue: asyncio.Queue | None = None
        self.stage: set[web.WebSocketResponse] = set()
        self.brain: aiohttp.ClientSession | None = None
        self.brain_ws: aiohttp.ClientWebSocketResponse | None = None
        self.brain_ready = asyncio.Event()
        self.recent: deque[tuple[str, float]] = deque(maxlen=200)
        self.served = 0
        self.dropped = 0
        self.last_reply_at = 0.0
        self.last_comment_at = time.time()
        self.browsing_future = None
        self.turn: asyncio.Future | None = None
        self.topic = "在线陪聊"
        self.stats = load_stats()

    def count_comment(self, user: str) -> None:
        self.stats["comments"] = int(self.stats.get("comments", 0)) + 1
        users = self.stats.setdefault("users", {})
        users[user] = int(users.get(user, 0)) + 1
        save_stats(self.stats)

    def count_gift(self, user: str, count: int = 1) -> None:
        self.stats["gifts"] = int(self.stats.get("gifts", 0)) + max(1, count)
        gifters = self.stats.setdefault("gifters", {})
        gifters[user] = int(gifters.get(user, 0)) + 1
        save_stats(self.stats)

    # ---------------------------------------------------------------- stage
    def q(self) -> asyncio.Queue:
        if self.queue is None:
            self.queue = asyncio.Queue(maxsize=QUEUE_MAX)
        return self.queue

    async def broadcast(self, payload: dict) -> None:
        dead = []
        for ws in self.stage:
            if ws.closed:
                dead.append(ws)
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.stage.discard(ws)

    # ----------------------------------------------------------------- brain
    async def brain_loop(self) -> None:
        self.brain = aiohttp.ClientSession()
        while True:
            try:
                async with self.brain.ws_connect(BRAIN_WS, heartbeat=25) as ws:
                    self.brain_ws = ws
                    await ws.send_json(
                        {
                            "type": "hello",
                            "user_id": "stream",
                            "user_name": "观众",
                            "scene": {
                                "places": [
                                    {"id": "living", "name": "客厅"},
                                    {"id": "balcony", "name": "阳台"},
                                ],
                                "objects": [
                                    {"id": "sofa", "name": "沙发", "place": "living", "affords": ["sit"]},
                                    {"id": "plant", "name": "绿萝", "place": "balcony", "affords": ["look_at"]},
                                ],
                                "actions": ["nod", "shake_head", "jump", "look_at", "wave", "sit", "stand_up"],
                            },
                        }
                    )
                    self.brain_ready.set()
                    log("brain connected")
                    async for msg in ws:
                        if msg.type != WSMsgType.TEXT:
                            continue
                        try:
                            data = json.loads(msg.data)
                        except json.JSONDecodeError:
                            continue
                        if data.get("type") == "browsing":
                            if self.browsing_future and not self.browsing_future.done():
                                self.browsing_future.set_result(data)
                            continue
                        if data.get("type") == "turn":
                            if self.turn and not self.turn.done():
                                self.turn.set_result(data)
                            elif data.get("autonomous"):
                                self.last_reply_at = time.time()
                                await self.broadcast({
                                    "type": "turn",
                                    "say": data.get("say", []),
                                    "expression": data.get("expression", "neutral"),
                                    "actions": [],
                                    "looking_at": data.get("looking_at", ""),
                                    "autonomous": True,
                                })
            except Exception as error:  # noqa: BLE001
                log("brain error:", type(error).__name__, error)
            finally:
                self.brain_ready.clear()
                self.brain_ws = None
            log("brain disconnected, retry in 3s")
            await asyncio.sleep(3)

    async def browse_loop(self) -> None:
        """安静时高频换页面，偶尔对当前页面嘟囔一句。"""
        ticks = 0
        while True:
            await asyncio.sleep(BROWSE_TICK)
            try:
                if not self.brain_ready.is_set() or self.brain_ws is None:
                    continue
                if time.time() - self.last_comment_at < BROWSE_QUIET:
                    continue
                if self.turn and not self.turn.done():
                    continue

                self.browsing_future = asyncio.get_running_loop().create_future()
                await self.brain_ws.send_json({"type": "browse_tick"})
                try:
                    page = await asyncio.wait_for(self.browsing_future, timeout=30)
                except asyncio.TimeoutError:
                    page = {"ok": False}
                finally:
                    self.browsing_future = None

                if page.get("ok"):
                    ticks += 1
                    await self.broadcast({
                        "type": "browsing",
                        "site": page.get("site", ""),
                        "title": page.get("title", ""),
                        "summary": page.get("summary", ""),
                        "url": page.get("url", ""),
                    })
                    if ticks % BROWSE_COMMENT_EVERY == 0:
                        await self.brain_ws.send_json({"type": "browse_comment"})
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                log("browse loop error:", type(error).__name__, error)

    # ------------------------------------------------------------- consumer
    async def consume(self) -> None:
        log("consumer started")
        while True:
            item = await self.q().get()
            log("dequeued", item["text"][:30])
            try:
                await self.brain_ready.wait()
                gap = time.time() - self.last_reply_at
                if gap < MIN_GAP:
                    await asyncio.sleep(MIN_GAP - gap)

                self.turn = asyncio.get_running_loop().create_future()
                assert self.brain_ws is not None
                await self.brain_ws.send_json(
                    {"type": "user_text", "text": item["text"], "place": "living"}
                )
                turn = await asyncio.wait_for(self.turn, timeout=TURN_TIMEOUT)
                self.turn = None
                self.last_reply_at = time.time()
                self.served += 1
                log("reply", " | ".join(x.get("text", "") for x in turn.get("say", [])))
                await self.broadcast(
                    {
                        "type": "turn",
                        "say": turn.get("say", []),
                        "expression": turn.get("expression", ""),
                        "actions": turn.get("actions", []),
                        "looking_at": turn.get("looking_at", ""),
                        "reply_to": item["user"],
                    }
                )
            except asyncio.TimeoutError:
                log("turn timeout")
                self.turn = None
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                log("consume error:", type(error).__name__, error)
                self.turn = None
            finally:
                self.q().task_done()


BRIDGE = Bridge()


async def handle_comment(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        data = {}
    user = clean(data.get("user") or data.get("nickname") or "观众")[:24] or "观众"
    text = clean(data.get("text") or data.get("content") or "")
    if not text:
        return web.json_response({"ok": False, "error": "empty"}, status=400)

    now = time.time()
    for seen, at in BRIDGE.recent:
        if seen == text and now - at < DEDUPE_WINDOW:
            BRIDGE.dropped += 1
            return web.json_response({"ok": False, "error": "duplicate"})
    BRIDGE.recent.append((text, now))

    if BRIDGE.q().full():
        BRIDGE.dropped += 1
        return web.json_response({"ok": False, "error": "queue full"}, status=429)

    BRIDGE.last_comment_at = time.time()
    BRIDGE.count_comment(user)
    await BRIDGE.q().put({"user": user, "text": text})
    await BRIDGE.broadcast({"type": "comment", "user": user, "text": text})
    return web.json_response({"ok": True, "queued": BRIDGE.q().qsize()})


async def handle_say(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        data = {}
    user = clean(data.get("user") or "观众")[:24] or "观众"
    text = clean(data.get("text") or "")
    if not text:
        return web.json_response({"ok": False, "error": "empty"}, status=400)
    if BRIDGE.q().full():
        return web.json_response({"ok": False, "error": "queue full"}, status=429)
    BRIDGE.last_comment_at = time.time()
    BRIDGE.count_comment(user)
    await BRIDGE.q().put({"user": user, "text": text})
    await BRIDGE.broadcast({"type": "comment", "user": user, "text": text})
    return web.json_response({"ok": True, "queued": BRIDGE.q().qsize()})


async def handle_gift(request: web.Request) -> web.Response:
    """B 站礼物 / 上舰：记一次互动，画面自己决定播什么动作。"""
    try:
        data = await request.json()
    except Exception:
        data = {}
    user = clean(data.get("user") or "")[:24] or "观众"
    gift = clean(data.get("gift") or "")[:24] or "礼物"
    try:
        count = max(1, int(data.get("count") or 1))
    except (TypeError, ValueError):
        count = 1
    BRIDGE.last_comment_at = time.time()
    BRIDGE.count_gift(user, count)
    await BRIDGE.broadcast({"type": "gift", "user": user, "gift": gift, "count": count})
    return web.json_response({"ok": True})


async def handle_event(request: web.Request) -> web.Response:
    """一次性动作：wave / nod / think 等，播一遍就回常驻状态。"""
    try:
        data = await request.json()
    except Exception:
        data = {}
    name = str(data.get("name") or "").strip()
    if name not in {"wave", "nod", "think", "heart", "clap",
                    "laugh", "sigh", "surprised", "sad",
                    "shake_head", "jump", "stretch", "yawn",
                    "touch_head", "fix_hair", "blow_kiss", "kiss"}:
        return web.json_response({"ok": False, "error": "unknown event"}, status=400)
    await BRIDGE.broadcast({"type": "action", "name": name})
    return web.json_response({"ok": True, "event": name})


async def handle_scene(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        data = {}
    name = str(data.get("name") or "").strip()
    if name not in {"idle", "talk", "work"}:
        return web.json_response({"ok": False, "error": "unknown scene"}, status=400)
    BRIDGE.topic = {"idle": "在线陪聊", "work": "在摸鱼", "talk": "说话中"}[name]
    await BRIDGE.broadcast({"type": "scene", "name": name})
    await BRIDGE.broadcast({"type": "state", "topic": BRIDGE.topic})
    return web.json_response({"ok": True, "scene": name})


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)
    BRIDGE.stage.add(ws)
    await ws.send_json({"type": "state", "topic": BRIDGE.topic})
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "hello":
                await ws.send_json({"type": "state", "topic": BRIDGE.topic})
    finally:
        BRIDGE.stage.discard(ws)
    return ws


async def handle_stats(request: web.Request) -> web.Response:
    """给画面用：今日 token 用量，顺带大脑是否在线。"""
    users = sorted(BRIDGE.stats.get("users", {}).items(), key=lambda kv: -kv[1])[:8]
    out = {
        "ok": True,
        "tokens": {},
        "brain": BRIDGE.brain_ready.is_set(),
        "stats": {
            "date": BRIDGE.stats.get("date", ""),
            "comments": int(BRIDGE.stats.get("comments", 0)),
            "gifts": int(BRIDGE.stats.get("gifts", 0)),
            "people": len(BRIDGE.stats.get("users", {})),
            "users": [{"name": name, "count": int(n)} for name, n in users],
        },
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://127.0.0.1:6200/health", timeout=5) as resp:
                if resp.status < 400:
                    data = await resp.json()
                    out["tokens"] = data.get("tokens") or {}
    except Exception:  # noqa: BLE001
        pass
    return web.json_response(out, headers={"Access-Control-Allow-Origin": "*"})


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": True,
            "brain": BRIDGE.brain_ready.is_set(),
            "queue": BRIDGE.q().qsize(),
            "served": BRIDGE.served,
            "dropped": BRIDGE.dropped,
            "stages": len(BRIDGE.stage),
        }
    )


async def handle_index(request: web.Request) -> web.StreamResponse:
    raise web.HTTPFound("/stream/stream.html")


async def on_startup(app: web.Application) -> None:
    app["tasks"] = [
        asyncio.create_task(BRIDGE.brain_loop()),
        asyncio.create_task(BRIDGE.consume()),
        asyncio.create_task(BRIDGE.browse_loop()),
    ]


async def on_cleanup(app: web.Application) -> None:
    for task in app["tasks"]:
        task.cancel()
    if BRIDGE.brain:
        await BRIDGE.brain.close()


def main() -> None:
    app = web.Application(client_max_size=1 << 20)
    app.router.add_post("/comment", handle_comment)
    app.router.add_post("/gift", handle_gift)
    app.router.add_post("/say", handle_say)
    app.router.add_post("/scene", handle_scene)
    app.router.add_post("/event", handle_event)
    app.router.add_get("/ws", handle_ws)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/stats", handle_stats)
    app.router.add_get("/", handle_index)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    log(f"bridge on :{PORT}  brain={BRAIN_WS}")
    web.run_app(app, host="0.0.0.0", port=PORT, print=None)


if __name__ == "__main__":
    main()
