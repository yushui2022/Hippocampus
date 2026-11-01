"""Rebuild embedding vectors after changing the embedding model.

    python -m hippocampus.rebuild --db memory.db
    python -m hippocampus.rebuild --db memory.db --scope user_42 --provider local
    python -m hippocampus.rebuild --db memory.db --provider openai --api-key sk-...

Vectors from different models are not comparable, even at the same dimension.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from .kernel import Hippocampus
from .retrieval import HashingEmbedder, LocalEmbedder, OpenAIEmbedder
from .store import Store


def _embedder(args):
    if args.provider == "hashing":
        return HashingEmbedder(dim=args.dim or 256)
    if args.provider == "local":
        return LocalEmbedder(
            base_url=args.base or "http://127.0.0.1:11434/v1",
            model=args.model or "bge-m3",
            dim=args.dim or 1024,
        )
    key = args.api_key or os.environ.get("HIPPO_EMBED_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("--api-key or HIPPO_EMBED_KEY is required for openai")
    return OpenAIEmbedder(
        api_key=key,
        model=args.model or "BAAI/bge-m3",
        base_url=args.base or "https://api.siliconflow.cn/v1",
        dim=args.dim or 1024,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild Hippocampus vectors")
    parser.add_argument("--db", required=True)
    parser.add_argument("--scope", default="")
    parser.add_argument("--provider", choices=["local", "openai", "hashing"], default="local")
    parser.add_argument("--base", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--dim", type=int, default=0)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--no-shadow", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    store = Store(args.db)
    try:
        scopes = [args.scope] if args.scope else store.distinct_scopes()
        if not scopes:
            print("no engrams found")
            return 0
        print("scopes: {}".format(", ".join(scopes)))
        print("vectors now: {}".format(store.vector_count(args.scope or None)))
        if args.dry_run:
            for scope in scopes:
                print("  {}: {} engrams".format(scope, len(store.list(scope))))
            return 0

        embedder = _embedder(args)
        print("embedder: {} (dim={})".format(embedder.model, embedder.dim))
        for scope in scopes:
            hippo = Hippocampus(scope=scope, store=store, embedder=embedder, auto_index=False)
            result = hippo.rebuild_vectors(
                scope=scope,
                batch_size=args.batch_size,
                shadow=not args.no_shadow,
                progress=lambda done, total: print(
                    "\r  {}: {}/{}".format(scope, done, total), end="", flush=True
                ),
            )
            print(
                "\r  {}: {} vectors, {} empty skipped".format(
                    scope, result["vectors"], result["skipped_empty"]
                )
            )
        print("done")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
