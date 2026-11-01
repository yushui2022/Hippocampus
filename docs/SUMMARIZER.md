# LLM summarizer

> The prompt is the instruction; the chat model is what turns it into a
> memory. bge-m3 cannot do this job — it only produces vectors.

## What it does

When an episode is written (or an old engram is consolidated), the kernel can
send the recent turns to a chat model and store a **first-person memory**
instead of a truncated first sentence.

```
10 turns → prompt + dialogue → chat model → JSON
        → {gist, key_facts, topics, sentiment, importance, emotion}
        → engram.gist, gist_source="llm", meta["topics"], meta["key_facts"]
```

The embedding model is separate and unchanged.

## Setup

```bash
export DEEPSEEK_API_KEY=sk-...
```

```python
from hippocampus import Event, Hippocampus, LLMSummarizer

summarizer = LLMSummarizer.from_env()
hippo = Hippocampus(
    scope="user_42",
    path="memory.db",
    embedder=LocalEmbedder(),     # vectors
    summarizer=summarizer,        # memory writing
    async_summary=True,           # default for file DBs
    persona="温柔、嘴硬、爱吃白饭的蓝色鲸鱼娘",
)
```

`from_env()` reads `DEEPSEEK_API_KEY` (or `HIPPO_SUMMARY_KEY` /
`OPENAI_API_KEY`) and defaults to:

```
base_url = https://api.deepseek.com/v1
model    = deepseek-chat
```

Any OpenAI-compatible chat endpoint works; override with
`HIPPO_SUMMARY_BASE` / `HIPPO_SUMMARY_MODEL`.

## What the prompt enforces

`hippocampus/prompts/summarize.txt`:

- first person, the character's voice
- the other speaker's **actual nickname** (never "用户/对方")
- relative time → absolute date
- only information present in the dialogue; no invention
- fixed JSON shape

The caller can pass `persona` for tone. `Event.meta["sender_name"]` (or
`nickname`) is what the prompt will use for the other speaker; without it the
label is "你".

## Fallbacks

| Failure | Behaviour |
| --- | --- |
| API error / timeout | retry, then leave the extractive gist |
| invalid JSON | retry, then `SummaryError`; caller keeps extractive |
| no summarizer | `first_clause` (v0 behaviour) |
| async queue full | drop, keep extractive, no blocking |

A summary is **never** a fact source. Facts still come only from user
utterances.

## Cost and latency

- One call per episode / consolidation, not per message.
- DeepSeek `deepseek-chat`: ~1.4 s for a 4-turn episode on this machine.
- Input ~500–1500 tokens, output ~150–300 tokens.
- `async_summary=True` (default for file DBs): `remember_episode()` returns in
  ~0.4 ms; the call runs in the background and is drained on `recall()` /
  `close()`.
- Synchronous mode is for tests and CLIs.

## Example output

Input (4 turns, sender name 阿澈):

> 我数学考砸了，哭了好久…… / 别这么说，一次考试说明不了什么…… /
> 最近一直学不进去，晚上也睡不好 / 今晚先别学了……

`gist`:

> 三月二日晚上，阿澈跟我说数学考砸了，哭了好久，觉得自己特别没用。
> 我告诉她一次考试说明不了什么，愿意一起看看错在哪。她又说最近学不进去、
> 晚上睡不好，我便让她今晚先别学，吃点东西早点睡，明天陪她从最薄弱的知识点
> 开始。

`topics`: `["考试失利", "情绪安慰", "学习计划"]`
`emotion`: `难过`, `importance`: `0.7`

Compare with the extractive path: `我数学考砸了`.

## Security

- Never commit the key; `.env` is ignored.
- The prompt contains raw conversation text. If that is sensitive, point
  `base_url` at a local Ollama chat model instead of a cloud API.
- Rotate the key if it has been pasted into a chat or log.
