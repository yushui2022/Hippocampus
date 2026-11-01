"""A five-minute tour of Hippocampus v2.

    python examples/companion_demo.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hippocampus import Event, HashingEmbedder, Hippocampus, snapshot

T0 = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)


def day(n: float) -> datetime:
    return T0 + timedelta(days=n)


def main() -> None:
    path = str(Path(__file__).with_name("demo.db"))
    hippo = Hippocampus(
        scope="demo",
        profile="companion",
        path=path,
        embedder=HashingEmbedder(),
    )

    # --- 1. a few days of conversation -----------------------------------
    script = [
        (0, "我数学考砸了，哭了好久，觉得自己特别没用"),
        (0, "我不吃香菜，闻到就难受"),
        (2, "我拿到那家公司的 offer 了！！开心到睡不着"),
        (5, "我养了一只猫，叫团子"),
        (9, "下周三我要去杭州入职"),
    ]
    for offset, text in script:
        hippo.remember(Event(text, at=day(offset)))

    # --- 2. advance time, one real day at a time -------------------------
    for offset in range(1, 31):
        current = day(offset)
        hippo.tick(
            current,
            active=True,
            active_days={day(offset - 1).date(), current.date()},
        )

    now = day(30)

    # --- 3. passive recall: the user asked -------------------------------
    print("\n== recall('你还记得我考试的事吗') ==")
    for hit in hippo.recall("你还记得我考试的事吗", budget=4, now=now):
        print("  L{}  {:.2f}  {}".format(hit.level, hit.score, hit.text))

    # --- 4. proactive: what would she bring up on her own? ---------------
    print("\n== salient() ==")
    for hit in hippo.salient(budget=3, now=now):
        print("  L{}  {:.2f}  {}".format(hit.level, hit.score, hit.text))
        hippo.mark_mentioned([hit.engram.engram_id], now=now)

    # --- 5. facts: only user speech, always sourced ----------------------
    print("\n== active facts ==")
    for fact in hippo.facts(now=now):
        print(
            "  {:<10} {} {}  (ttl={}d, source={})".format(
                fact.kind, fact.predicate, fact.obj, int(fact.ttl_days), fact.source_engram
            )
        )

    # --- 6. read-only look at the same file ------------------------------
    print("\n== snapshot ==")
    data = snapshot(path)
    print("  counts:", data["counts"])
    print("  schema:", data["schema_version"])

    # --- 7. delete something, hard ---------------------------------------
    victim = hippo.recall("猫", budget=1, now=now)
    if victim:
        hippo.forget(victim[0].engram.engram_id, hard=True)
        print("\n== after hard delete ==")
        print("  counts:", snapshot(path)["counts"])

    hippo.close()


if __name__ == "__main__":
    main()
