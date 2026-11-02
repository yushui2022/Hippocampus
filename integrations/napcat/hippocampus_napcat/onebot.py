from __future__ import annotations

import asyncio
import json
import uuid
from typing import Dict, Optional

from aiohttp import web


class OneBotConnection:
    """OneBot v11 reverse WebSocket connection + action requests."""

    def __init__(self) -> None:
        self.ws: Optional[web.WebSocketResponse] = None
        self.pending: Dict[str, asyncio.Future] = {}

    @property
    def connected(self) -> bool:
        return self.ws is not None and not self.ws.closed

    def attach(self, ws: web.WebSocketResponse) -> None:
        self.ws = ws

    def detach(self, ws: web.WebSocketResponse) -> None:
        if self.ws is ws:
            self.ws = None
        for future in self.pending.values():
            if not future.done():
                future.set_exception(RuntimeError("napcat disconnected"))
        self.pending.clear()

    async def send_action(self, action: str, params: dict, timeout: float = 20.0):
        if not self.connected:
            raise RuntimeError("napcat websocket is not connected")
        echo = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending[echo] = future
        await self.ws.send_str(
            json.dumps({"action": action, "params": params, "echo": echo})
        )
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(echo, None)

    async def send_text(
        self, *, group_id: str = "", user_id: str = "", text: str
    ) -> None:
        params = {"message": [{"type": "text", "data": {"text": text}}]}
        if group_id:
            params["group_id"] = int(group_id)
            await self.send_action("send_group_msg", params)
        else:
            params["user_id"] = int(user_id)
            await self.send_action("send_private_msg", params)

    def handle_payload(self, data: dict) -> Optional[dict]:
        echo = data.get("echo")
        if echo and echo in self.pending:
            future = self.pending[echo]
            if not future.done():
                future.set_result(data)
            return None
        return data
