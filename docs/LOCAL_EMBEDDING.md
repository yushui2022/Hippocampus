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
| `recall@6 (clear)` | 0.733 | 0.733 |
| `fog_accuracy` | 1.000 | 1.000 |
| `fog_missing` | 0.000 | 0.133 |
| `over_recall` | 0.100 | 0.050 |
| `para_recall@6` | 0.600 (hashing) | **1.000** |
| recall p50 | 0.4 ms | 18.7 ms |

Two different things are being measured:

- **Retrieval**: `para_recall` goes from 0.600 (hashing) to **1.000**; bge-m3
  finds paraphrases that share no tokens.
- **Forgetting calibration**: `recall@6 (clear)` counts memories still at
  L0/L1 after 30 days. bge-m3 does not change decay, so this number stays at
  0.733. Fixing it is an importance/stability calibration task, not an
  embedding task.

`fog_missing` moves from 0.000 to 0.133 on this synthetic set; two of fifteen
fuzzy probes rank just outside the top 6. Treat it as a tuning target, not a
regression of the embedding itself.

## 5. Cost, privacy, and consistency

- **Cost**: one embedding per new memory and one per user turn. On this machine
  a query takes ~15–20 ms; no tokens are billed.
- **Privacy**: text goes to `127.0.0.1` only.
- **Memory**: the model needs roughly 1.2 GB of disk and about 1–2 GB of RAM
  while loaded.
- **Consistency**: changing the embedding model changes the vector space.
  Hippocampus records a fingerprint and marks `needs_vector_rebuild`, but the
  rebuild script is still a P1.5 item. Until it exists, keep the model fixed.

## 6. When not to use it

If you only need names, places and numbers to be recalled, the token/BM25
baseline is enough and starts in milliseconds. Add bge-m3 when users start
saying "最近学不动了" instead of "数学考砸了".
