import sys, pathlib, tempfile, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from hippocampus_napcat.config import Config
from hippocampus_napcat.memory import MemoryService


def test_record_and_recall_without_embedder():
    path = tempfile.mktemp(suffix=".db")
    cfg = Config(memory_db=path, embed_model="", api_key="")
    mem = MemoryService(cfg, embedder=None, summarizer=None)
    scope = "qq:FriendMessage:1"
    mem.record(scope, "我喜欢喝冰美式，不加糖", sender_name="斯高")
    mem.hippo(scope).flush_episode()
    hits = mem.recall(scope, "我喜欢喝什么", 3)
    assert any("冰美式" in h.text for h in hits)
    mem.close()
    os.unlink(path)
