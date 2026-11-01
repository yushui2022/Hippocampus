"""Local bge-m3 via Ollama, no API key.

    ollama pull bge-m3
    ollama serve
    python examples/local_embedding.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hippocampus import Event, Hippocampus, LocalEmbedder, cosine

T0 = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)


def main() -> None:
    embedder = LocalEmbedder()

    a, b = embedder.embed(["我数学考砸了，哭了好久", "最近学不动了"])
    print("dim          ", len(a))
    print("cosine       ", round(cosine(a, b), 4))

    hippo = Hippocampus(
        scope="local-demo",
        path=str(Path(__file__).with_name("local_demo.db")),
        embedder=embedder,
    )
    hippo.remember(Event("我数学考砸了，哭了好久", at=T0))
    hippo.remember(Event("我养了一只猫叫团子", at=T0))
    hippo.remember(Event("我不吃香菜", at=T0))

    print("\nquery: 最近学不动了")
    for hit in hippo.recall("最近学不动了", budget=3, now=T0):
        print("  L{}  {:.2f}  {}".format(hit.level, hit.score, hit.text))

    hippo.close()


if __name__ == "__main__":
    main()
