import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from hippocampus_napcat.config import Config
from hippocampus_napcat.events import parse_message, should_interject


def test_parse_private():
    e = parse_message({
        "post_type": "message", "message_type": "private",
        "self_id": "1", "user_id": "2", "message_id": "m1",
        "sender": {"nickname": "斯高"},
        "message": [{"type": "text", "data": {"text": "你好"}}],
    })
    assert e and e.scope == "qq:FriendMessage:2" and e.text == "你好"


def test_parse_group_at_and_image():
    e = parse_message({
        "post_type": "message", "message_type": "group",
        "self_id": "1", "user_id": "2", "group_id": "99", "message_id": "m2",
        "sender": {"card": "阿澈"},
        "message": [
            {"type": "at", "data": {"qq": "1"}},
            {"type": "text", "data": {"text": "看图"}},
            {"type": "image", "data": {"url": "https://x/y.png"}},
        ],
    })
    assert e and e.scope == "qq:GroupMessage:99"
    assert e.at_self and e.images == ["https://x/y.png"] and e.sender_name == "阿澈"


def test_self_message_is_ignored():
    assert parse_message({
        "post_type": "message", "message_type": "private",
        "self_id": "1", "user_id": "1", "message": [],
    }) is None


def test_interject_only_test_group():
    cfg = Config(test_group="99", capture_groups=frozenset({"99"}), active_chance=1.0)
    e = parse_message({
        "post_type": "message", "message_type": "group",
        "self_id": "1", "user_id": "2", "group_id": "99", "message_id": "m3",
        "sender": {"nickname": "a"},
        "message": [{"type": "text", "data": {"text": "随便聊聊"}}],
    })
    assert e and should_interject(e, cfg, 0.1)
    cfg2 = Config(test_group="100", capture_groups=frozenset({"100"}), active_chance=1.0)
    assert not should_interject(e, cfg2, 0.1)
