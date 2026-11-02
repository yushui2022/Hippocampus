import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hippocampus_napcat.style import clean_reply, greeting_reply, is_greeting, split_reply


def test_greeting():
    assert is_greeting("你好！！")
    assert is_greeting("在吗")
    assert not is_greeting("你好，今天新闻")
    assert greeting_reply("晚安") in {"睡吧。", "晚安。"}


def test_identity_guard():
    assert clean_reply("我是桃子姐姐，不是模型", "你是ai吗") in {
        "你猜。", "不是，别问。", "问这个干嘛。"
    }


def test_split_single_and_multi():
    first, extras = split_reply("好的。", multi_chance=0, essay_chance=0)
    assert first == "好的。" and extras == []
    first, extras = split_reply(
        "第一句。第二句。第三句。第四句。",
        multi_chance=1.0, essay_chance=0, max_chars=60,
    )
    assert first and extras and len(extras) <= 2
