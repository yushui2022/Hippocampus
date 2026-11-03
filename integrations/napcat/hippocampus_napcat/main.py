from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from .bridge import Bridge
from .config import Config


def log(*args) -> None:
    print("[napcat-bridge {}]".format(time.strftime("%H:%M:%S")), *args, flush=True)


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    bridge: Bridge = request.app["bridge"]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    bridge.onebot.attach(ws)
    log("napcat connected")
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            payload = bridge.onebot.handle_payload(data)
            if payload is not None:
                asyncio.create_task(bridge.handle(payload))
    finally:
        bridge.onebot.detach(ws)
        log("napcat disconnected")
    return ws


async def health(request: web.Request) -> web.Response:
    bridge: Bridge = request.app["bridge"]
    stats = bridge.memory.brain.stats()
    return web.json_response(
        {
            "ok": True,
            "napcat": bridge.onebot.connected,
            "scope": bridge.memory.brain.scope,
            "engrams": stats.get("total"),
            "buffer": stats.get("buffer_pending"),
            "entities": stats.get("entities"),
        }
    )


async def status(request: web.Request) -> web.Response:
    bridge: Bridge = request.app["bridge"]
    return web.json_response(
        {
            "napcat": bridge.onebot.connected,
            "status": bridge.memory.status(),
        }
    )


async def on_startup(app: web.Application) -> None:
    bridge = Bridge(app["config"])
    app["bridge"] = bridge
    app["maintenance"] = asyncio.create_task(bridge.maintenance())
    log(
        "listening on {}:{} model={} db={}".format(
            app["config"].host,
            app["config"].port,
            app["config"].chat_model,
            app["config"].memory_db,
        )
    )


async def on_cleanup(app: web.Application) -> None:
    task = app.get("maintenance")
    if task:
        task.cancel()
    bridge = app.get("bridge")
    if bridge:
        await bridge.close()


def create_app(cfg: Config) -> web.Application:
    app = web.Application()
    app["config"] = cfg
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/health", health)
    app.router.add_get("/status", status)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    cfg = Config.from_env()
    Path(cfg.memory_db).parent.mkdir(parents=True, exist_ok=True)
    web.run_app(create_app(cfg), host=cfg.host, port=cfg.port, print=None)


if __name__ == "__main__":
    main()
