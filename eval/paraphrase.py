"""Paraphrase recall benchmark.

The v0 benchmark probes with the original wording. Real users paraphrase:
"最近学不动了" should still reach "数学考砸了".

Two modes:

    python -m eval.paraphrase                 # deterministic lexicon embedder
    HIPPO_EMBED_KEY=... python -m eval.paraphrase --embedder openai

The lexicon mode exists to test the *pipeline* (candidate union → RRF →
rerank). It is not a model benchmark; always report which mode produced a
number.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

try:
    from hippocampus import Event, HashingEmbedder, Hippocampus, OpenAIEmbedder
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from hippocampus import Event, HashingEmbedder, Hippocampus, OpenAIEmbedder

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)

PAIRS: List[Tuple[str, str]] = [
    ("我数学考砸了，哭了好久", "最近学不动了"),
    ("我爸妈上个月离婚了", "家里出了点事"),
    ("我拿到那家公司的offer了", "工作有着落了吗"),
    ("我养了一只猫叫团子", "我家宠物最近怎么样"),
    ("我确诊了焦虑症", "最近精神状态不太好"),
    ("我下个月要去杭州工作", "搬家的事"),
    ("我和男朋友分手了", "感情的事"),
    ("我奶奶住院了", "家里老人身体"),
    ("我考研失败了", "考试的事"),
    ("我最近在学吉他", "最近在玩什么乐器"),
]


class LexiconEmbedder:
    """Deterministic synonym clusters, for pipeline tests only."""

    model = "lexicon-eval"
    dim = 12
    CLUSTERS = [
        ("数学", "考砸", "学不动", "考试", "考研", "成绩"),
        ("爸妈", "离婚", "家里", "家人", "奶奶", "住院"),
        ("offer", "工作", "公司", "面试", "上班", "杭州"),
        ("猫", "宠物", "团子", "养"),
        ("焦虑", "精神", "状态", "难过", "哭"),
        ("男朋友", "分手", "感情", "恋爱"),
        ("吉他", "乐器", "音乐"),
        ("搬家", "搬", "杭州"),
    ]

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            lowered = text.lower()
            for index, words in enumerate(self.CLUSTERS):
                if any(word in lowered for word in words):
                    vec[index] = 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


def build_embedder(name: str):
    if name == "openai":
        key = os.environ.get("HIPPO_EMBED_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise SystemExit("HIPPO_EMBED_KEY is not set")
        return OpenAIEmbedder(
            api_key=key,
            model=os.environ.get("HIPPO_EMBED_MODEL", "BAAI/bge-m3"),
            base_url=os.environ.get("HIPPO_EMBED_BASE", "https://api.siliconflow.cn/v1"),
        )
    if name == "hashing":
        return HashingEmbedder()
    return LexiconEmbedder()


def run(embedder_name: str = "lexicon", budget: int = 6) -> dict:
    embedder = build_embedder(embedder_name)
    h = Hippocampus(scope="para", path=":memory:", embedder=embedder)
    ids = []
    for index, (memory, _query) in enumerate(PAIRS):
        e = h.remember(Event(memory, at=T0 + timedelta(hours=index)))
        ids.append(e.engram_id)

    hits = 0
    misses = []
    for index, (_memory, query) in enumerate(PAIRS):
        result = h.recall(query, budget=budget, now=T0 + timedelta(days=1))
        if any(hit.engram.engram_id == ids[index] for hit in result):
            hits += 1
        else:
            misses.append((query, [hit.text for hit in result]))

    return {
        "embedder": embedder_name,
        "pairs": len(PAIRS),
        "budget": budget,
        "para_recall_at_k": round(hits / float(len(PAIRS)), 3),
        "misses": misses,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Paraphrase recall benchmark")
    parser.add_argument(
        "--embedder", choices=["lexicon", "hashing", "openai"], default="lexicon"
    )
    parser.add_argument("--budget", type=int, default=6)
    args = parser.parse_args(argv)
    result = run(args.embedder, args.budget)
    print("embedder            {}".format(result["embedder"]))
    print("pairs               {}".format(result["pairs"]))
    print("para_recall@{}       {:.3f}".format(result["budget"], result["para_recall_at_k"]))
    if result["misses"]:
        print("\nmisses:")
        for query, texts in result["misses"]:
            print("  {!r} -> {}".format(query, texts[:3]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
