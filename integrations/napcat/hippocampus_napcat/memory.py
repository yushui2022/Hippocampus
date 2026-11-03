from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from hippocampus import Brain, LLMSummarizer, LocalEmbedder, Store, Utterance

from .config import Config
from .events import MessageEvent


class MemoryService:
    """One Brain per bot.

    DMs and groups are not separate memories: they are separate *situations*
    inside one self, distinguished by source. That is the v3 model.
    """

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
        self.brain = Brain(
            scope=cfg.brain_scope,
            store=self.store,
            profile=cfg.profile,
            summarizer=self.summarizer,
            embedder=self.embedder,
            persona=cfg.persona,
            async_index=True,
            async_summary=True,
        )

    # -- writes --------------------------------------------------------------
    def observe(
        self,
        event: MessageEvent,
        *,
        witnesses: Optional[List[str]] = None,
        context_size: int = 2,
    ):
        return self.brain.observe(
            Utterance(
                text=event.text or ("[图片]" if event.images else ""),
                context_id=event.context_id,
                context_kind="group" if event.is_group else "dyad",
                context_size=max(2, context_size),
                speaker_id=event.speaker_id,
                speaker_name=event.sender_name,
                actor="user",
                addressed=event.addressed(self.cfg.bot_names),
                witnesses=list(witnesses or [event.speaker_id]),
                refs=[event.message_id] if event.message_id else [],
                at=datetime.now(timezone.utc),
            )
        )

    def observe_assistant(self, event: MessageEvent, text: str) -> None:
        if not text.strip():
            return
        self.brain.observe(
            Utterance(
                text=text,
                context_id=event.context_id,
                context_kind="group" if event.is_group else "dyad",
                context_size=2,
                speaker_id="self",
                speaker_name=self.cfg.bot_names[0] if self.cfg.bot_names else "我",
                actor="assistant",
                addressed=False,
                witnesses=[event.speaker_id],
                at=datetime.now(timezone.utc),
            )
        )
        self.brain.mark_replied(event.context_id, event.speaker_id)

    # -- reads ---------------------------------------------------------------
    def context(
        self,
        query: str,
        *,
        context_id: str,
        present: Optional[List[str]] = None,
        budget: int = 2,
    ):
        return self.brain.context(
            query,
            context_id=context_id,
            present=present or [],
            budget=budget,
        )

    def self_block(self) -> str:
        return self.brain.self_model().render(
            min_evidence=self.cfg.card_min_evidence
        )

    # -- maintenance ---------------------------------------------------------
    def maintain(self) -> Dict[str, object]:
        return self.brain.maintain()

    def sleep(self, *, context_id: Optional[str] = None, force: bool = True):
        return self.brain.sleep(context_id=context_id, force=force)

    def reflect(self, *, force: bool = True) -> bool:
        return self.brain.reflect(force=force)

    def status(self) -> Dict[str, object]:
        stats = self.brain.stats()
        stats["self_model"] = self.self_block()
        stats["people"] = [
            {
                "id": entity.entity_id,
                "name": entity.name,
                "familiarity": round(entity.familiarity, 3),
                "notes": len(entity.card.get("notes") or []),
            }
            for entity in self.brain.people()
        ]
        return stats

    def close(self) -> None:
        try:
            self.brain.close()
        finally:
            self.store.close()
