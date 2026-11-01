"""Latency / throughput benchmark.

    python -m eval.bench --n 1000
    python -m eval.bench --n 10000 --embedder none
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

try:
    from hippocampus import Event, HashingEmbedder, Hippocampus
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from hippocampus import Event, HashingEmbedder, Hippocampus

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _pct(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * p)))
    return ordered[index]


def run(n: int = 1000, embedder_name: str = "hashing") -> dict:
    embedder = HashingEmbedder() if embedder_name == "hashing" else None
    h = Hippocampus(scope="bench", path=":memory:", embedder=embedder)
    remember_ms: List[float] = []
    recall_ms: List[float] = []

    for i in range(n):
        unique = "".join(
            chr(0x4E00 + (i * 7 + k * 37) % 20000) for k in range(10)
        )
        text = "{}：今天聊了工作、朋友和晚饭，心情还不错".format(unique)
        start = time.perf_counter()
        h.remember(Event(text, at=T0 + timedelta(hours=i % 240)))
        remember_ms.append((time.perf_counter() - start) * 1000.0)

    h.tick(T0 + timedelta(days=1))
    for i in range(min(200, n)):
        start = time.perf_counter()
        h.recall("工作 朋友 晚饭", budget=6)
        recall_ms.append((time.perf_counter() - start) * 1000.0)

    start = time.perf_counter()
    h.tick(T0 + timedelta(days=30), active=False)
    tick_ms = (time.perf_counter() - start) * 1000.0

    return {
        "n": n,
        "embedder": embedder_name,
        "remember_p50_ms": round(statistics.median(remember_ms), 3),
        "remember_p95_ms": round(_pct(remember_ms, 0.95), 3),
        "recall_p50_ms": round(statistics.median(recall_ms), 3) if recall_ms else 0.0,
        "recall_p95_ms": round(_pct(recall_ms, 0.95), 3),
        "tick_30d_ms": round(tick_ms, 3),
        "engrams": h.count(),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Hippocampus latency benchmark")
    parser.add_argument("--n", type=int, default=1000)
    parser.add_argument("--embedder", choices=["hashing", "none"], default="hashing")
    args = parser.parse_args(argv)
    result = run(n=args.n, embedder_name=args.embedder)
    for key, value in result.items():
        print("{:<18} {}".format(key, value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
