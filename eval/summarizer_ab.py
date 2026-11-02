"""LLM-judged A/B: summarized memory vs verbatim memory.

    DEEPSEEK_API_KEY=... python -m eval.summarizer_ab

A = Hippocampus with `LLMSummarizer` (first-person gist)
B = the exact original sentence

A separate DeepSeek call judges which snippet reads more like a human
recollection. This is **LLM-as-judge**, not a human blind test; use
`eval.blind_test` for the human version.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

try:
    from eval import synth
    from hippocampus import Event, Hippocampus
    from hippocampus.summarizer import LLMSummarizer
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from eval import synth
    from hippocampus import Event, Hippocampus
    from hippocampus.summarizer import LLMSummarizer

UTC = timezone.utc

JUDGE_PROMPT = """你是盲测裁判。下面是一个用户问题和两个"角色回忆片段"。
请判断哪一个更像真人回想起来的东西，而不是数据库摘录。

考虑：
- 第一人称、自然的口吻
- 有具体信息，但不机械罗列
- 该模糊的地方允许模糊，不假装全记得
- 不编造对话里没有的事实

只输出 JSON：{{"winner": "1" 或 "2", "reason": "一句话理由"}}

用户问题：{query}

候选 1：
{first}

候选 2：
{second}
"""


def _chat(api_key: str, messages: List[Dict[str, str]], base: str, model: str) -> str:
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 200,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
    )
    with urllib.request.urlopen(request, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


def _parse_winner(text: str):
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None, ""
    winner = str(data.get("winner", "")).strip()
    reason = str(data.get("reason", "")).strip()
    return (winner if winner in ("1", "2") else None), reason


def build(api_key: str, persona: str) -> tuple:
    turns, facts = synth.build(days=30, seed=7)
    summarizer = LLMSummarizer(api_key=api_key)
    hippo = Hippocampus(
        scope="ab",
        path=":memory:",
        summarizer=summarizer,
        async_summary=False,
        persona=persona,
    )

    by_day: Dict[object, List[synth.Turn]] = {}
    for turn in turns:
        by_day.setdefault(turn.at.date(), []).append(turn)

    for day in sorted(by_day):
        for turn in by_day[day]:
            hippo.feed_episode(
                Event(turn.text, at=turn.at, meta={"fact": turn.fact_key}),
                max_turns=10,
            )
        hippo.flush_episode()
        last = by_day[day][-1].at
        hippo.tick(last + timedelta(minutes=1), active=True)

    episode_count = len(hippo.store.list("ab"))

    queries = synth.queries(facts, days=30)
    probe_now = datetime.combine(
        max(by_day) + timedelta(days=1), datetime.min.time(), tzinfo=UTC
    ) + timedelta(hours=21)

    # Controlled comparison: the episode that actually contains the fact,
    # summarized gist vs the exact sentence. Retrieval is measured elsewhere.
    by_fact = {}
    for engram in hippo.store.list("ab"):
        for key in engram.meta.get("facts", []):
            if key and (key not in by_fact or engram.gist_source == "llm"):
                by_fact[key] = engram

    pairs = []
    rng = random.Random(11)
    for group in ("remember", "fog"):
        for query, fact in queries[group]:
            engram = by_fact.get(fact.key)
            if engram is None:
                a_text = "（没想起来）"
            elif engram.gist_source == "llm" and engram.gist:
                a_text = engram.gist
            else:
                a_text = engram.render(now=probe_now)
            pairs.append(
                {
                    "group": group,
                    "query": query,
                    "a": a_text,
                    "b": fact.text,
                    "a_first": bool(rng.getrandbits(1)),
                }
            )
    hippo.close()
    return pairs, episode_count


def judge(
    api_key: str, pairs: List[dict], base: str, model: str
) -> Dict[str, object]:
    votes = {"A": 0, "B": 0, "skip": 0}
    rows = []
    for index, pair in enumerate(pairs, 1):
        first = pair["a"] if pair["a_first"] else pair["b"]
        second = pair["b"] if pair["a_first"] else pair["a"]
        prompt = JUDGE_PROMPT.format(
            query=pair["query"], first=first, second=second
        )
        try:
            raw = _chat(
                api_key,
                [{"role": "user", "content": prompt}],
                base,
                model,
            )
            winner, reason = _parse_winner(raw)
        except Exception as exc:  # noqa: BLE001
            rows.append({"index": index, "error": str(exc)})
            votes["skip"] += 1
            continue
        if winner is None:
            votes["skip"] += 1
            rows.append({"index": index, "raw": raw, "choice": "skip"})
            continue
        choice = "A" if ((winner == "1") == bool(pair["a_first"])) else "B"
        votes[choice] += 1
        rows.append(
            {
                "index": index,
                "group": pair["group"],
                "query": pair["query"],
                "a": pair["a"],
                "b": pair["b"],
                "choice": choice,
                "reason": reason,
            }
        )
    decided = votes["A"] + votes["B"]
    return {
        "pairs": len(pairs),
        "votes": votes,
        "a_win_rate": round(votes["A"] / decided, 3) if decided else 0.0,
        "rows": rows,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="LLM-judged summarizer A/B")
    parser.add_argument("--json", default="")
    parser.add_argument("--base", default="https://api.deepseek.com/v1")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--persona", default="温柔、嘴硬、爱吃白饭的蓝色鲸鱼娘")
    args = parser.parse_args(argv)

    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY is not set")

    pairs, episodes = build(key, args.persona)
    print("episodes summarized:", episodes)
    print("pairs:", len(pairs))
    result = judge(key, pairs, args.base, args.model)
    print("votes:", result["votes"])
    print("A (summarized) win rate: {:.3f}".format(result["a_win_rate"]))
    for row in result["rows"][:6]:
        print("\n---", row.get("group"), "|", row.get("query"))
        print("A:", row.get("a"))
        print("B:", row.get("b"))
        print("judge:", row.get("choice"), "-", row.get("reason"))

    if args.json:
        Path(args.json).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("wrote", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
