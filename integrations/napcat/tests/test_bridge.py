import sys, pathlib, tempfile, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

import pytest

from hippocampus_napcat.bridge import Bridge
from hippocampus_napcat.config import Config


def _msg(text, message_id="m1", group="", user="2"):
    data = {
        "post_type": "message",
        "message_type": "group" if group else "private",
        "self_id": "1",
        "user_id": user,
        "message_id": message_id,
        "sender": {"nickname": "斯高"},
        "message": [{"type": "text", "data": {"text": text}}],
    }
    if group:
        data["group_id"] = group
    return data


@pytest.mark.asyncio
async def test_private_message_captures_memory():
    path = tempfile.mktemp(suffix=".db")
    cfg = Config(memory_db=path, embed_model="", api_key="", test_group="99")
    bridge = Bridge(cfg)
    sent = []

    async def fake_send_text(*, group_id="", user_id="", text=""):
        sent.append(text)

    async def fake_reply(event, *, empty_mention=False):
        sent.append("REPLY:" + event.text)

    bridge.onebot.send_text = fake_send_text
    bridge.chat.reply = fake_reply
    await bridge.handle(_msg("记住我喜欢冰美式"))
    bridge.memory.hippo("qq:FriendMessage:2").flush_episode()
    assert sent and sent[0].startswith("REPLY:")
    assert len(bridge.memory.store.list("qq:FriendMessage:2")) >= 1
    await bridge.close()
    os.unlink(path)


@pytest.mark.asyncio
async def test_hmem_status_command():
    path = tempfile.mktemp(suffix=".db")
    cfg = Config(memory_db=path, embed_model="", api_key="", test_group="99")
    bridge = Bridge(cfg)
    sent = []

    async def fake_send_text(*, group_id="", user_id="", text=""):
        sent.append(text)

    bridge.onebot.send_text = fake_send_text
    await bridge.handle(_msg("/hmem"))
    assert sent and "记忆" in sent[0]
    await bridge.close()
    os.unlink(path)
