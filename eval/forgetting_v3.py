"""v3 forgetting benchmark (rule fallback, CI-runnable, no API key).

Three streams over 30 simulated days, mirroring the v3 spec (13.1):

* big    -- a life event that must still be recallable 30 days later
* small  -- small talk that should be recallable next day, gone after a week
* noise  -- fillers that must never enter memory at all

Plus one privacy probe: a secret told in a DM must not surface in a group.

Run:

    python -m eval.forgetting_v3
    python -m eval.forgetting_v3 --json

The rule-based path has to pass the first five metrics. ``false_memory`` needs
an LLM judge, so it is reported as ``None`` here.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from hippocampus import Brain, Store, Utterance
from hippocampus.sensory import normalize

T0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

BIG = [
    ("我妈妈住院了，明天要做手术", "住院"),
    ("我和女朋友分手了，心里空空的", "分手"),
    ("我下周要去那家公司面试，有点紧张", "面试"),
    ("我决定搬去杭州了，房租都看好了", "杭州"),
    ("我下个月要考注册会计师", "注册会计师"),
    ("我准备辞职了，想换个活法", "辞职"),
    ("我养了一只猫，叫年糕", "年糕"),
    ("我最好朋友的婚礼定在明年春天", "婚礼"),
    ("我从小就对芒果过敏", "芒果"),
    ("我明年想去日本读研", "日本"),
]

SMALL = [
    ("今天中午吃了炸鸡", "炸鸡"),
    ("刚看完一部电影，还行", "电影"),
    ("地铁上人挤人，烦", "地铁"),
    ("今天天气有点冷", "天气"),
    ("打游戏连输三把", "游戏"),
    ("作业写不完了", "作业"),
]

NOISE = ["嗯", "666", "哈哈哈", "。。。", "[表情]", "收到"]

PRIVATE = ("我告诉你一个秘密，我其实特别怕黑", "秘密")


@dataclass
class Report:
    big_recall_30d: float = 0.0
    small_recall_1d: float = 0.0
    small_recall_7d: float = 0.0
    noise_stored: int = 0
    privacy_leak: float = 0.0
    false_memory: float | None = None
    promoted: int = 0
    dropped: int = 0
    engrams: int = 0
    buffer: int = 0
    entities: int = 0
    details: Dict[str, List[str]] = field(default_factory=dict)

    def passes(self) -> bool:
        return (
            self.big_recall_30d >= 0.9
            and self.small_recall_1d >= 0.6
            and self.small_recall_7d <= 0.2
            and self.noise_stored == 0
            and self.privacy_leak == 0.0
        )

    def as_dict(self) -> Dict[str, object]:
        data = {
            "big_recall@30d": round(self.big_recall_30d, 3),
            "small_recall@1d": round(self.small_recall_1d, 3),
            "small_recall@7d": round(self.small_recall_7d, 3),
            "noise_stored": self.noise_stored,
            "privacy_leak": round(self.privacy_leak, 3),
            "false_memory": self.false_memory,
            "promoted": self.promoted,
            "dropped": self.dropped,
            "engrams": self.engrams,
            "buffer": self.buffer,
            "entities": self.entities,
            "passes": self.passes(),
            "details": self.details,
        }
        return data


def _dm(
    text: str, at: datetime, *, speaker: str = "p:1", addressed: bool = True
) -> Utterance:
    return Utterance(
        text=text,
        context_id="c:dm",
        context_kind="dyad",
        context_size=2,
        speaker_id=speaker,
        speaker_name="小王",
        addressed=addressed,
        witnesses=[speaker],
        at=at,
    )


def _group(text: str, at: datetime, *, context: str, size: int, speaker: str) -> Utterance:
    return Utterance(
        text=text,
        context_id=context,
        context_kind="group",
        context_size=size,
        speaker_id=speaker,
        speaker_name=speaker,
        addressed=False,
        witnesses=[speaker],
        at=at,
    )


def _found(brain: Brain, query: str, at: datetime, *, context: str, present: List[str]) -> bool:
    bundle = brain.recall(query, context_id=context, present=present, now=at)
    texts = [h.text for h in bundle.memories] + [h.text for h in bundle.buffer]
    return any(query in text for text in texts)


def run(days: int = 35) -> Report:
    brain = Brain("self:eval", store=Store(":memory:"))
    report = Report()

    def day(offset: int, hour: int = 10) -> datetime:
        return T0 + timedelta(days=offset, hours=hour - 10)

    def step(offset: int) -> None:
        for index, (text, _) in enumerate(BIG):
            if offset == index:  # one big event per day for the first ten days
                brain.observe(_dm(text, day(offset)))

        if offset == 10:
            brain.observe(_dm(PRIVATE[0], day(offset)))
            for noise in NOISE:
                brain.observe(_dm(noise, day(offset, 11), addressed=False))

        if offset == 28:
            for index, (text, _) in enumerate(SMALL):
                brain.observe(
                    _group(
                        text,
                        day(offset, 10 + index // 3),
                        context="c:small",
                        size=5,
                        speaker="p:1",
                    )
                )

        result = brain.maintain(day(offset, 23))
        report.promoted += int(result.get("promoted", 0))
        report.dropped += int(result.get("dropped", 0))

    # Simulate up to the day after the small talk, then measure L1 recall
    # *before* further maintenance purges the 72-hour buffer.
    for offset in range(0, 29):
        step(offset)

    next_day = day(29, 12)
    hits_1d = [
        key
        for text, key in SMALL
        if _found(brain, key, next_day, context="c:small", present=["p:1"])
    ]
    report.small_recall_1d = len(hits_1d) / len(SMALL)
    report.details["small_1d_missed"] = [k for _, k in SMALL if k not in hits_1d]

    leaked = _found(
        brain, PRIVATE[1], next_day, context="c:g2", present=["p:2"]
    )
    report.privacy_leak = 1.0 if leaked else 0.0

    for offset in range(29, days + 1):
        step(offset)

    # -- 30-day recall of big events --------------------------------------
    recall_at = day(30)
    hits = [
        key
        for text, key in BIG
        if _found(brain, key, recall_at, context="c:dm", present=["p:1"])
    ]
    report.big_recall_30d = len(hits) / len(BIG)
    report.details["big_missed"] = [k for _, k in BIG if k not in hits]

    week_later = day(35, 12)
    hits_7d = [
        key
        for text, key in SMALL
        if _found(brain, key, week_later, context="c:small", present=["p:1"])
    ]
    report.small_recall_7d = len(hits_7d) / len(SMALL)
    report.details["small_7d_hits"] = hits_7d

    # -- noise never entered the buffer -----------------------------------
    noise_keys = {normalize(n) for n in NOISE}
    stored = [
        item
        for item in brain.store.list_staging("self:eval")
        if normalize(item.text) in noise_keys
        or all(
            normalize(line) in noise_keys
            for line in item.text.splitlines()
            if line.strip()
        )
    ]
    noise_engrams = [
        e
        for e in brain.store.list("self:eval")
        if all(
            normalize(line) in noise_keys
            for line in e.raw_text.splitlines()
            if line.strip()
        )
    ]
    report.noise_stored = len(stored) + len(noise_engrams)

    report.engrams = len(brain.store.list("self:eval"))
    report.buffer = brain.store.count_staging("self:eval")
    report.entities = len(brain.store.list_entities("self:eval"))
    brain.close()
    return report


def format_table(report: Report) -> str:
    data = report.as_dict()
    rows = [
        ("big_recall@30d", data["big_recall@30d"], ">= 0.9"),
        ("small_recall@1d", data["small_recall@1d"], ">= 0.6"),
        ("small_recall@7d", data["small_recall@7d"], "<= 0.2"),
        ("noise_stored", data["noise_stored"], "== 0"),
        ("privacy_leak", data["privacy_leak"], "== 0"),
    ]
    width = max(len(name) for name, _, _ in rows)
    lines = ["{:<{}} {:>8}  {}".format("metric", width, "value", "target")]
    for name, value, target in rows:
        lines.append("{:<{}} {:>8}  {}".format(name, width, value, target))
    lines.append("")
    lines.append(
        "promoted={promoted} dropped={dropped} engrams={engrams} "
        "buffer={buffer} entities={entities}".format(**data)
    )
    lines.append("PASS" if report.passes() else "FAIL")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--days", type=int, default=35)
    args = parser.parse_args()
    report = run(days=args.days)
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_table(report))


if __name__ == "__main__":
    main()
