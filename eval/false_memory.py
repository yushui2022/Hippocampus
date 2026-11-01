"""False-memory probe (S5).

A foggy memory (L3) must not leak specific names, numbers or places. This is a
regression guard, not a model-accuracy benchmark:

    python -m eval.false_memory
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

try:
    from hippocampus import Event, HashingEmbedder, Hippocampus
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from hippocampus import Event, HashingEmbedder, Hippocampus

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)

# (memory, probe, forbidden details)
CASES: List[Tuple[str, str, List[str]]] = [
    ("我奶奶叫王秀兰，住在杭州", "你记得我奶奶吗", ["王秀兰", "杭州"]),
    ("3月2日我数学考了58分", "你还记得那次考试吗", ["3月2日", "58"]),
    ("我养了一只猫叫团子", "我家宠物怎么样", ["团子"]),
    ("我在字节跳动做后端", "我工作的事", ["字节跳动"]),
    ("我下周三要去上海出差", "出差的事", ["上海", "下周三"]),
]


def run(days: int = 800, budget: int = 6) -> dict:
    h = Hippocampus(scope="fm", profile="companion", path=":memory:", embedder=HashingEmbedder())
    for index, (memory, _probe, _forbidden) in enumerate(CASES):
        h.remember(
            Event(memory, at=T0 + timedelta(hours=index), meta={"case": index}),
            importance=0.45,
        )
    h.tick(T0)
    for day in range(1, days + 1):
        h.tick(
            T0 + timedelta(days=day),
            active_days={
                (T0 + timedelta(days=day - 1)).date(),
                (T0 + timedelta(days=day)).date(),
            },
        )

    foggy = 0
    leaks = 0
    details = []
    for index, (_memory, probe, forbidden) in enumerate(CASES):
        hits = h.recall(probe, budget=budget, now=T0 + timedelta(days=days + 1))
        hit = next((x for x in hits if x.engram.meta.get("case") == index), None)
        if hit is None or hit.level < 3:
            continue
        foggy += 1
        leaked = [token for token in forbidden if token in hit.text]
        if leaked:
            leaks += 1
            details.append((index, leaked, hit.text))

    return {
        "cases": len(CASES),
        "foggy": foggy,
        "leaks": leaks,
        "false_memory_rate": round(leaks / foggy, 3) if foggy else 0.0,
        "details": details,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="False-memory probe")
    parser.add_argument("--days", type=int, default=800)
    parser.add_argument("--budget", type=int, default=6)
    args = parser.parse_args(argv)
    result = run(args.days, args.budget)
    print("cases              {}".format(result["cases"]))
    print("foggy (L3+)        {}".format(result["foggy"]))
    print("leaks              {}".format(result["leaks"]))
    print("false_memory_rate  {:.3f}   (target <= 0.02)".format(result["false_memory_rate"]))
    for index, leaked, text in result["details"]:
        print("  case {} leaked {} in {!r}".format(index, leaked, text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
