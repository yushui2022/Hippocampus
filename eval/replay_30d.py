"""30-day replay benchmark.

This is a **retrieval-level** benchmark, not an answer-accuracy benchmark:
we check whether the right memory surfaces, at the right level of clarity.
It needs no LLM API key, so it runs in CI.

    python -m eval.replay_30d
    python -m eval.replay_30d --days 30 --seed 7 --budget 6 --json out.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:  # running as a module
    from eval import synth
    from hippocampus import Event, Hippocampus
except ImportError:  # running as a script
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from eval import synth
    from hippocampus import Event, Hippocampus

UTC = timezone.utc


def run(
    days: int = 30,
    seed: int = 7,
    budget: int = 6,
    profile: str = "companion",
    db: str = ":memory:",
) -> Dict[str, object]:
    turns, facts = synth.build(days=days, seed=seed)

    hippo = Hippocampus(scope="eval", profile=profile, path=db)

    by_day: Dict[object, List[synth.Turn]] = {}
    for turn in turns:
        by_day.setdefault(turn.at.date(), []).append(turn)

    for day in sorted(by_day):
        for turn in by_day[day]:
            hippo.remember(
                Event(text=turn.text, at=turn.at, meta={"fact": turn.fact_key})
            )
        last = by_day[day][-1].at
        hippo.tick(now=last + timedelta(minutes=1), active=True)
        hippo.consolidate(now=last + timedelta(minutes=2))

    probe_now = datetime.combine(
        max(by_day) + timedelta(days=1), dtime(hour=21), tzinfo=UTC
    )

    latencies: List[float] = []
    clear = foggy = 0
    remember_missing = 0
    fog_missing_count = 0
    surfaced_forget = 0
    fog_too_clear = 0

    queries = synth.queries(facts, days=days)

    for query, fact in queries["remember"]:
        hits = _timed_recall(hippo, query, budget, probe_now, latencies)
        hit = _find(hits, fact.key)
        if hit is None:
            remember_missing += 1
        elif hit.level <= 1:
            clear += 1
        else:
            foggy += 1

    for query, fact in queries["fog"]:
        hits = _timed_recall(hippo, query, budget, probe_now, latencies)
        hit = _find(hits, fact.key)
        if hit is None:
            fog_missing_count += 1
        elif hit.level >= 1:
            foggy += 1
        else:
            fog_too_clear += 1

    for query, fact in queries["forget"]:
        hits = _timed_recall(hippo, query, budget, probe_now, latencies)
        if _find(hits, fact.key) is not None:
            surfaced_forget += 1

    levels = _levels_by_group(hippo, facts)

    n_remember = len(queries["remember"])
    n_fog = len(queries["fog"])
    n_forget = len(queries["forget"])

    fog_retrieved = foggy + fog_too_clear
    metrics = {
        "recall_at_k": clear / n_remember if n_remember else 0.0,
        "fog_accuracy": foggy / fog_retrieved if fog_retrieved else 0.0,
        "fog_missing": fog_missing_count / n_fog if n_fog else 0.0,
        "over_recall": surfaced_forget / n_forget if n_forget else 0.0,
        "remember_missing": remember_missing / n_remember if n_remember else 0.0,
        "latency_p50_ms": round(statistics.median(latencies) * 1000, 2) if latencies else 0.0,
    }

    samples = _samples(hippo, facts, queries, probe_now, budget)

    return {
        "config": {
            "days": days,
            "seed": seed,
            "budget": budget,
            "profile": profile,
            "turns": len(turns),
            "engrams": hippo.count(),
        },
        "metrics": metrics,
        "levels": levels,
        "samples": samples,
    }


def report(result: Dict[str, object], stream=sys.stdout) -> None:
    cfg = result["config"]  # type: ignore[index]
    m = result["metrics"]  # type: ignore[index]
    levels = result["levels"]  # type: ignore[index]

    out = []
    out.append("")
    out.append("=== Hippocampus · {}-day replay ===".format(cfg["days"]))
    out.append(
        "turns={turns}  engrams={engrams}  profile={profile}  budget={budget}  seed={seed}".format(
            **cfg
        )
    )
    out.append("")
    out.append("group      n    L0   L1+   mean_level")
    for group in ("remember", "fog", "forget"):
        row = levels.get(group, {})  # type: ignore[union-attr]
        out.append(
            "{:<10} {:<4} {:<4} {:<5} {:.2f}".format(
                group,
                row.get("n", 0),
                row.get("l0", 0),
                row.get("l1plus", 0),
                row.get("mean", 0.0),
            )
        )

    out.append("")
    out.append("metric              value    target")
    out.append(
        "recall@{} (clear)    {:.3f}    >= 0.85".format(
            cfg["budget"], m["recall_at_k"]
        )
    )
    out.append("fog_accuracy        {:.3f}    >= 0.60".format(m["fog_accuracy"]))
    out.append("fog_missing         {:.3f}    (lower better)".format(m["fog_missing"]))
    out.append("over_recall         {:.3f}    <= 0.15   <- uncanny valley".format(m["over_recall"]))
    out.append("latency p50         {:.1f}ms".format(m["latency_p50_ms"]))

    out.append("")
    out.append("--- how memories surface on day {} ---".format(cfg["days"] + 1))
    for group in ("remember", "fog", "forget"):
        out.append("[{}]".format(group))
        for sample in result["samples"].get(group, []):  # type: ignore[union-attr]
            out.append(
                "  L{}  {:.2f}  {}".format(
                    sample["level"], sample["score"], sample["text"]
                )
            )
    out.append("")

    stream.write("\n".join(out))


def _timed_recall(hippo, query, budget, now, sink: List[float]):
    started = time.perf_counter()
    hits = hippo.recall(query, budget=budget, now=now, reinforce=False)
    sink.append(time.perf_counter() - started)
    return hits


def _find(hits, key: str):
    for hit in hits:
        if hit.engram.meta.get("fact") == key:
            return hit
    return None


def _levels_by_group(hippo, facts) -> Dict[str, Dict[str, float]]:
    group_of = {f.key: f.group for f in facts}
    bucket: Dict[str, List[int]] = {"remember": [], "fog": [], "forget": []}
    for engram in hippo.store.list(hippo.scope):
        key = engram.meta.get("fact")
        group = group_of.get(key)
        if group:
            bucket[group].append(engram.level)

    out: Dict[str, Dict[str, float]] = {}
    for group, ls in bucket.items():
        out[group] = {
            "n": len(ls),
            "l0": sum(1 for x in ls if x == 0),
            "l1plus": sum(1 for x in ls if x >= 1),
            "mean": round(sum(ls) / len(ls), 2) if ls else 0.0,
        }
    return out


def _samples(hippo, facts, queries, now, budget):
    out: Dict[str, List[Dict[str, object]]] = {}
    by_group: Dict[str, List[Tuple[str, object]]] = {}
    for group in ("remember", "fog", "forget"):
        by_group[group] = queries[group][:3]

    for group, probes in by_group.items():
        rows = []
        for query, fact in probes:
            hits = hippo.recall(query, budget=budget, now=now, reinforce=False)
            hit = _find(hits, fact.key)
            if hit is not None:
                rows.append(
                    {
                        "level": hit.level,
                        "score": round(hit.score, 2),
                        "text": hit.text,
                    }
                )
        out[group] = rows
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="30-day memory replay benchmark")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--budget", type=int, default=6)
    parser.add_argument("--profile", default="companion")
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)

    result = run(
        days=args.days, seed=args.seed, budget=args.budget, profile=args.profile
    )
    report(result)
    if args.json:
        Path(args.json).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("wrote {}".format(args.json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
