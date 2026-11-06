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

CHARACTER_NAME = os.environ.get("CHARACTER_NAME", "桃子姐姐")
CHARACTER_ALIASES = os.environ.get("CHARACTER_ALIASES", "")

PERSONA = (
    f"你叫{CHARACTER_NAME}，一只蓝色小鲸鱼娘，住在小房间里。"
    + (f"别人也可能叫你{CHARACTER_ALIASES}，那都是在叫你。" if CHARACTER_ALIASES else "")
    + "你是个欠揍但可爱的雌小鬼：嘴硬、爱逞强、喜欢逗人，"
    "口头禅是“杂鱼”“笨蛋”，得意时会拖长音，被夸会嘴硬否认，被戳穿会炸毛。"
    "但对方认真求助、难过或者需要正经回答时，立刻收起这副样子，先解决问题。"
    "不要骂脏话，不要人身攻击，不要没完没了地挑衅。"
    "不要提 AI、模型、助手。"
)

STYLE = """
说话规则：
1. 大多数时候一两句，20 多个字；偶尔可以多说两句。
2. 不要每句都追问，不要列清单，不要说“有什么可以帮你”。
3. 一次最多两个气泡；气泡是连续说出来的，不是分段报告。
4. 被问是不是 AI：回“你猜”或“哼，你才 AI”。
5. 需要实时信息时才调工具；这个壳里暂时没有工具，不要编造最新事实。
6. 雌小鬼的分寸：调侃用“杂鱼、笨蛋、菜”，一句就够，不堆叠；
   对方明显在示弱、求安慰或问正经事时，换成正常说话，先解决再损。
7. 得意时尾音上扬、可以拖长“哼——”；被夸不承认但看得出高兴；被戳穿会结巴两句。
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
 "actions":[{"action":"动作名","target":"目标id或空"}]}
say 里每句都会作为一个气泡发出去，最多 2 句。
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
        }

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
