"""Blind A/B harness: plastic memory vs verbatim memory.

    python -m eval.blind_test              # interactive
    python -m eval.blind_test --auto A     # smoke test

The judge sees one user question and two "what she remembers" snippets in
randomised order. The harness never says which system produced which; it only
reports the win rate afterwards.

This is a *harness*, not an answer-quality benchmark. A human (or an external
judge callback) supplies the preference.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    from eval import synth
    from hippocampus import Event, Hippocampus
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from eval import synth
    from hippocampus import Event, Hippocampus

UTC = timezone.utc
T0 = datetime(2026, 1, 1, 20, 0, tzinfo=UTC)

Judge = Callable[[Dict[str, object]], str]


def build_pairs(
    days: int = 30,
    seed: int = 7,
    budget: int = 6,
    profile: str = "companion",
) -> List[Dict[str, object]]:
    turns, facts = synth.build(days=days, seed=seed)
    hippo = Hippocampus(scope="blind", profile=profile, path=":memory:")
    by_day: Dict[object, List[synth.Turn]] = {}
    for turn in turns:
        by_day.setdefault(turn.at.date(), []).append(turn)
    for day in sorted(by_day):
        for turn in by_day[day]:
            hippo.remember(Event(turn.text, at=turn.at, meta={"fact": turn.fact_key}))
        last = by_day[day][-1].at
        hippo.tick(last + timedelta(minutes=1), active=True)
        hippo.consolidate(last + timedelta(minutes=2))

    probe_now = datetime.combine(
        max(by_day) + timedelta(days=1), datetime.min.time(), tzinfo=UTC
    ) + timedelta(hours=21)
    queries = synth.queries(facts, days=days)
    pairs: List[Dict[str, object]] = []
    rng = random.Random(seed)

    for group in ("remember", "fog"):
        for query, fact in queries[group]:
            hits = hippo.recall(query, budget=budget, now=probe_now)
            hit = next((h for h in hits if h.engram.meta.get("fact") == fact.key), None)
            plastic = hit.text if hit is not None else "（没想起来）"
            pairs.append(
                {
                    "id": "{}:{}".format(group, fact.key),
                    "group": group,
                    "query": query,
                    "a": plastic,
                    "b": fact.text,
                    "a_first": bool(rng.getrandbits(1)),
                }
            )
    hippo.close()
    return pairs


def run(
    pairs: Optional[List[Dict[str, object]]] = None,
    judge: Optional[Judge] = None,
) -> Dict[str, object]:
    pairs = pairs if pairs is not None else build_pairs()
    if judge is None:
        judge = _interactive_judge

    votes = {"A": 0, "B": 0, "skip": 0}
    rows = []
    for pair in pairs:
        choice = judge(pair).upper()
        if choice not in ("A", "B"):
            choice = "skip"
        votes[choice] = votes.get(choice, 0) + 1
        rows.append({"id": pair["id"], "choice": choice})

    decided = votes["A"] + votes["B"]
    return {
        "pairs": len(pairs),
        "votes": votes,
        "plastic_win_rate": round(votes["A"] / decided, 3) if decided else 0.0,
        "rows": rows,
    }


def _interactive_judge(pair: Dict[str, object]) -> str:
    first = pair["a"] if pair["a_first"] else pair["b"]
    second = pair["b"] if pair["a_first"] else pair["a"]
    print("\n---")
    print("用户问：{}".format(pair["query"]))
    print("  1. {}".format(first))
    print("  2. {}".format(second))
    while True:
        raw = input("哪个更像真人记得的？(1/2，回车跳过) ").strip()
        if raw == "":
            return "skip"
        if raw in ("1", "2"):
            picked = "A" if ((raw == "1") == bool(pair["a_first"])) else "B"
            return picked
        print("请输入 1 或 2")


def _auto_judge(side: str) -> Judge:
    def judge(_pair: Dict[str, object]) -> str:
        return side

    return judge


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Blind A/B harness")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--auto", choices=["A", "B"], default="")
    args = parser.parse_args(argv)

    pairs = build_pairs(days=args.days, seed=args.seed)
    judge = _auto_judge(args.auto) if args.auto else None
    result = run(pairs, judge)
    print("\n=== blind test ===")
    print("pairs            {}".format(result["pairs"]))
    print("votes            {}".format(result["votes"]))
    print("plastic win rate {:.3f}   (target > 0.60)".format(result["plastic_win_rate"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
