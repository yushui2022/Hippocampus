# Local embeddings with Ollama + bge-m3

`bge-m3` is an **embedding** model, not a chat model. It turns text into 1024
numbers so the memory kernel can match "学不动了" with "数学考砸了". The chat
model stays whatever you already use (DeepSeek, Qwen, Claude...).

Running it locally means no API key, no per-query cost, and memory text never
leaves the machine.

## 1. Install Ollama

macOS app: <https://ollama.com/download>

Or the CLI only (what this machine uses):

```bash
mkdir -p ~/.local/ollama ~/.local/bin
curl -fL -o /tmp/ollama-darwin.tgz \
  https://gh-proxy.com/https://github.com/ollama/ollama/releases/download/v0.40.13/ollama-darwin.tgz
tar -xzf /tmp/ollama-darwin.tgz -C ~/.local/ollama
ln -sf ~/.local/ollama/ollama ~/.local/bin/ollama
```

Make sure `~/.local/bin` is on `PATH`.

## 2. Pull the model and start the server

```bash
ollama pull bge-m3        # ~1.2 GB
ollama serve              # if the desktop app is not already running
```

Verify the OpenAI-compatible endpoint:

```bash
curl -s http://127.0.0.1:11434/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"bge-m3","input":["我数学考砸了","最近学不动了"]}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['data'][0]['embedding']))"
# 1024
```

## 3. Use it from Hippocampus

```python
from hippocampus import Event, Hippocampus, LocalEmbedder

hippo = Hippocampus(
    scope="user_42",
    path="memory.db",
    embedder=LocalEmbedder(),   # http://127.0.0.1:11434/v1, model=bge-m3
    async_index=True,           # non-blocking writes (default for file DBs)
)
```

`LocalEmbedder` is a thin preset over `OpenAIEmbedder`:

```python
OpenAIEmbedder(
    api_key="ollama",
    model="bge-m3",
    base_url="http://127.0.0.1:11434/v1",
    dim=1024,
)
```

Run the demo:

```bash
python examples/local_embedding.py
```

## 4. Benchmarks on this machine

Apple Silicon, 64 GB, Ollama 0.40.13, bge-m3 1.2 GB, pure Python vector search.

| Metric | Token baseline | Local bge-m3 |
| --- | --- | --- |
| `recall@6 (clear)` | 0.933 | **1.000** |
| `fog_accuracy` | 1.000 | 1.000 |
| `fog_missing` | 0.000 | **0.000** |
| `over_recall` | 0.100 | 0.100 |
| `para_recall@6` | 0.600 (hashing) | **1.000** |
| recall p50 | 0.4 ms | 18.6 ms |

Two different things are being measured:

- **Retrieval**: `para_recall` goes from 0.600 (hashing) to **1.000**; bge-m3
  finds paraphrases that share no tokens, and `fog_missing` drops to 0.000.
- **Forgetting calibration**: `recall@6 (clear)` counts memories still at
  L0/L1 after 30 days. It was lifted to 1.000 by `importance_gain = 14` and
  one-level recovery when the user explicitly re-tells a memory. The embedding
  model is not responsible for that number.

The hashing baseline reaches 1.000 on `recall@6` but misses 4/15 fuzzy probes
(`fog_missing 0.267`) because it has no semantics. That is the difference
bge-m3 buys.

## 5. Cost, privacy, and consistency

- **Cost**: one embedding per new memory and one per user turn. On this machine
  a query takes ~15–20 ms; no tokens are billed. Writes are backgrounded:
  five `remember()` calls took 78 ms synchronously and 1.7 ms with
  `async_index=True` (vectors flush on `close()`).
- **Privacy**: text goes to `127.0.0.1` only.
- **Memory**: the model needs roughly 1.2 GB of disk and about 1–2 GB of RAM
  while loaded.
- **Consistency**: changing the embedding model changes the vector space.
  Hippocampus records a fingerprint and marks `needs_vector_rebuild`. Rebuild
  with:

  ```bash
  python -m hippocampus.rebuild --db memory.db --provider local
  ```

  The CLI re-embeds every non-empty memory in every scope and updates the
  fingerprint.

## 6. When not to use it

If you only need names, places and numbers to be recalled, the token/BM25
baseline is enough and starts in milliseconds. Add bge-m3 when users start
saying "最近学不动了" instead of "数学考砸了".
