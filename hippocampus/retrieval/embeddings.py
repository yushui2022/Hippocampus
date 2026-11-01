"""Embedding providers.

The kernel never imports a vendor SDK. Anything that implements ``Embedder``
works: a local model, an OpenAI-compatible endpoint, or the deterministic
``HashingEmbedder`` used by tests and offline installs.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import urllib.request
from typing import List, Protocol, Sequence

from ..tokens import tokenize


class Embedder(Protocol):
    model: str
    dim: int

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        ...


class HashingEmbedder:
    """Deterministic bag-of-tokens embedding.

    It is **not** semantic: it only knows token overlap. Its job is to keep the
    full hybrid pipeline testable with zero dependencies and zero network.
    For real paraphrase recall, pass an ``OpenAIEmbedder`` (bge-m3).
    """

    model = "hashing-v1"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        # Single CJK characters are useful for BM25 but noisy in a hashing
        # embedding; bigrams and words carry the signal.
        for token in _semantic_tokens(text):
            digest = hashlib.md5(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


def _semantic_tokens(text: str) -> List[str]:
    return [
        token
        for token in tokenize(text)
        if not (len(token) == 1 and "\u4e00" <= token <= "\u9fff")
    ]


class OpenAIEmbedder:
    """Any OpenAI-compatible ``/embeddings`` endpoint (bge-m3, etc.)."""

    def __init__(
        self,
        api_key: str,
        model: str = "BAAI/bge-m3",
        base_url: str = "https://api.siliconflow.cn/v1",
        dim: int = 1024,
        timeout: float = 30.0,
        retries: int = 3,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.dim = dim
        self.timeout = timeout
        self.retries = retries

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        payload = json.dumps({"model": self.model, "input": list(texts)}).encode("utf-8")
        last_error = None
        for attempt in range(self.retries):
            try:
                request = urllib.request.Request(
                    self.base_url + "/embeddings",
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + self.api_key,
                    },
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                data = sorted(body["data"], key=lambda d: d.get("index", 0))
                vectors = [list(map(float, d["embedding"])) for d in data]
                if vectors and not self.dim:
                    self.dim = len(vectors[0])
                return vectors
            except Exception as exc:  # noqa: BLE001 - provider errors are opaque
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(min(8.0, 0.5 * (2 ** attempt)))
        raise RuntimeError("embedding request failed: {}".format(last_error))


class LocalEmbedder(OpenAIEmbedder):
    """bge-m3 served by a local Ollama instance (no API key, no cloud)."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434/v1",
        model: str = "bge-m3",
        dim: int = 1024,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(
            api_key="ollama",
            model=model,
            base_url=base_url,
            dim=dim,
            timeout=timeout,
        )


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        x = a[i]
        y = b[i]
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / math.sqrt(na * nb)
