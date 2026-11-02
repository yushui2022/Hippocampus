from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from hippocampus import Event, Hippocampus, LLMSummarizer, LocalEmbedder, Store
from hippocampus.self_model import SelfModel, parse_reflection, reflection_prompt

from .config import Config


class MemoryService:
    """Owns all Hippocampus instances and the self-model per QQ scope."""

    def __init__(
        self,
        cfg: Config,
        *,
        embedder=None,
        summarizer=None,
        store: Optional[Store] = None,
    ) -> None:
        self.cfg = cfg
        Path(cfg.memory_db).parent.mkdir(parents=True, exist_ok=True)
        self.store = store or Store(cfg.memory_db)
        self.embedder = embedder
        if self.embedder is None and cfg.embed_model:
            self.embedder = LocalEmbedder(
                base_url=cfg.embed_base,
                model=cfg.embed_model,
                dim=cfg.embed_dim,
            )
        self.summarizer = summarizer
        if self.summarizer is None and cfg.api_key:
            self.summarizer = LLMSummarizer(
                api_key=cfg.api_key,
                base_url=cfg.chat_base,
                model=cfg.summary_model,
            )
        self.hippos: Dict[str, Hippocampus] = {}
        self.self_models: Dict[str, SelfModel] = {}
        self.active_days: Dict[str, Set] = defaultdict(set)

    def hippo(self, scope: str) -> Hippocampus:
        item = self.hippos.get(scope)
        if item is None:
            item = Hippocampus(
                scope=scope,
                store=self.store,
                embedder=self.embedder,
                summarizer=self.summarizer,
                persona=self.cfg.persona,
                async_index=True,
                async_summary=True,
            )
            self.hippos[scope] = item
        return item

    def self_model(self, scope: str) -> SelfModel:
        item = self.self_models.get(scope)
        if item is None:
            item = SelfModel(self.store, scope)
            self.self_models[scope] = item
        return item

    def record(
        self,
        scope: str,
        text: str,
        *,
        actor: str = "user",
        sender_name: str = "",
        refs: Optional[Iterable[str]] = None,
    ) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.active_days[scope].add(datetime.now(timezone.utc).date())
        self.hippo(scope).feed_episode(
            Event(
                text=text,
                actor=actor,
                at=datetime.now(timezone.utc),
                meta={"sender_name": sender_name or "你", "refs": list(refs or [])},
            ),
            max_turns=10,
        )

    def recall(self, scope: str, query: str, budget: int = 2):
        return self.hippo(scope).recall(query, budget=budget)

    def tick_all(self, now: Optional[datetime] = None) -> None:
        at = now or datetime.now(timezone.utc)
        for scope, hippo in list(self.hippos.items()):
            days = self.active_days.pop(scope, set())
            hippo.flush_episode()
            hippo.tick(at, active_days=days)
            hippo.consolidate(at)

    async def reflect(
        self, scope: str, *, force: bool = False, min_pending: int = 4
    ) -> bool:
        if self.summarizer is None:
            return False
        hippo = self.hippo(scope)
        model = self.self_model(scope)
        pending = model.pending(hippo.store.list(scope))
        if not pending or (not force and len(pending) < min_pending):
            return False
        raw = await asyncio.to_thread(
            self.summarizer.chat, reflection_prompt(model, pending)
        )
        model.merge(parse_reflection(raw))
        model.mark_reflected(pending)
        return True

    def status(self, scope: str) -> Dict[str, object]:
        hippo = self.hippo(scope)
        stats = hippo.stats()
        stats["self_model"] = self.self_model(scope).render()
        return stats

    def close(self) -> None:
        for hippo in self.hippos.values():
            try:
                hippo.flush_episode()
                hippo.close()
            except Exception:
                pass
        self.hippos.clear()
        self.store.close()
