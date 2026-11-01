"""DeepSeek-backed memory summarizer.

    export DEEPSEEK_API_KEY=sk-...
    python examples/deepseek_summary.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hippocampus import Event, Hippocampus, LLMSummarizer

T0 = datetime(2026, 3, 2, 20, 0, tzinfo=timezone.utc)


def main() -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("set DEEPSEEK_API_KEY first")

    summarizer = LLMSummarizer.from_env()
    hippo = Hippocampus(
        scope="demo",
        path=":memory:",
        summarizer=summarizer,
        async_summary=False,     # demo waits for the call; production uses True
        persona="温柔、嘴硬、爱吃白饭的蓝色鲸鱼娘",
    )

    events = [
        Event(
            "我数学考砸了，哭了好久，觉得自己特别没用",
            at=T0,
            meta={"sender_name": "阿澈"},
        ),
        Event(
            "别这么说，一次考试说明不了什么。你愿意的话，我们一起看看错在哪。",
            actor="assistant",
            at=T0 + timedelta(minutes=2),
        ),
        Event(
            "嗯……其实我最近一直学不进去，晚上也睡不好",
            at=T0 + timedelta(minutes=4),
            meta={"sender_name": "阿澈"},
        ),
        Event(
            "那今晚先别学了，吃点东西早点睡。明天我陪你从最薄弱的那个知识点开始。",
            actor="assistant",
            at=T0 + timedelta(minutes=5),
        ),
    ]

    engram = hippo.remember_episode(events, summarize=True)
    stored = hippo.store.get(engram.engram_id)

    print("gist_source :", stored.gist_source)
    print("gist        :", stored.gist)
    print("topics      :", stored.meta.get("topics"))
    print("key_facts   :", stored.meta.get("key_facts"))
    print("emotion     :", stored.emotion, " importance:", stored.importance)
    import dataclasses

    print()
    print("L1 preview  :", dataclasses.replace(stored, level=1).render())
    print()
    print("recall: 最近学不动了")
    for hit in hippo.recall("最近学不动了", budget=3, now=T0):
        print("  L{}  {:.2f}  {}".format(hit.level, hit.score, hit.text))

    hippo.close()


if __name__ == "__main__":
    main()
