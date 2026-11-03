import os
import pathlib
import sys
import tempfile

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
async def test_private_message_reaches_the_buffer():
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

    assert sent and sent[0].startswith("REPLY:")
    assert bridge.memory.brain.store.count_staging("self:taozi", "pending") == 1

    bridge.memory.sleep(force=True)
    engrams = bridge.memory.brain.store.list("self:taozi")
    assert any("冰美式" in e.raw_text for e in engrams)
    await bridge.close()
    os.unlink(path)


@pytest.mark.asyncio
async def test_group_message_uses_source_aware_context():
    path = tempfile.mktemp(suffix=".db")
    cfg = Config(memory_db=path, embed_model="", api_key="", test_group="99")
    bridge = Bridge(cfg)

    async def fake_send_text(*, group_id="", user_id="", text=""):
        pass

    async def fake_reply(event, *, empty_mention=False):
        pass

    bridge.onebot.send_text = fake_send_text
    bridge.chat.reply = fake_reply
    await bridge.handle(_msg("我妈妈住院了", message_id="m2", group="99"))
    bridge.memory.sleep(force=True)

    bundle = bridge.memory.context(
        "住院", context_id="qq:group:99", present=["qq:2"], budget=3
    )
    assert any("住院" in h.text for h in bundle.memories)
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
