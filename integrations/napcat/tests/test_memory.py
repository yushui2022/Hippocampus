import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from hippocampus_napcat.config import Config
from hippocampus_napcat.events import parse_message
from hippocampus_napcat.memory import MemoryService


def _event(text, user="2", group=""):
    data = {
        "post_type": "message",
        "message_type": "group" if group else "private",
        "self_id": "1",
        "user_id": user,
        "message_id": "m1",
        "sender": {"nickname": "斯高"},
        "message": [{"type": "text", "data": {"text": text}}],
    }
    if group:
        data["group_id"] = group
    return parse_message(data)


def test_observe_consolidate_and_recall():
    path = tempfile.mktemp(suffix=".db")
    cfg = Config(memory_db=path, embed_model="", api_key="")
    mem = MemoryService(cfg)
    event = _event("我喜欢喝冰美式，不加糖")
    result = mem.observe(event, witnesses=[event.speaker_id], context_size=2)
    assert result.kept

    mem.sleep(force=True)
    bundle = mem.context(
        "我喜欢喝什么",
        context_id=event.context_id,
        present=[event.speaker_id],
        budget=3,
    )
    assert any("冰美式" in hit.text for hit in bundle.memories)
    mem.close()
    os.unlink(path)


def test_private_event_is_not_recalled_in_group():
    path = tempfile.mktemp(suffix=".db")
    cfg = Config(memory_db=path, embed_model="", api_key="")
    mem = MemoryService(cfg)
    event = _event("我告诉你一个秘密，我其实怕黑")
    mem.observe(event, witnesses=[event.speaker_id], context_size=2)
    mem.sleep(force=True)

    bundle = mem.context(
        "秘密", context_id="qq:group:777", present=["qq:999"], budget=3
    )
    assert not any("秘密" in hit.text for hit in bundle.memories)
    mem.close()
    os.unlink(path)
