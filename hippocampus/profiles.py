"""Profiles: the only place domain knowledge is allowed to live.

The kernel must never branch on a profile name. If a new product needs new
behaviour, it adds a profile -- it does not edit the kernel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass(frozen=True)
class Profile:
    name: str
    levels: int  # 5 -> L0..L4, 3 -> L0..L2
    importance_weights: Dict[str, float]
    retrieval_weights: Dict[str, float]
    cooldown_hours: float = 24.0
    idle_ratio: float = 0.05  # how fast memory ages while the user is away
    enable_relationship: bool = False
    gist_style: str = "neutral"
    # Near-identical text above this similarity is treated as a re-telling of
    # the same event (rehearsal), not a new one (pattern separation).
    merge_threshold: float = 0.85
    # Retrievability thresholds for L1..L4. A knowledge base and a companion
    # must not share the same curve.
    level_thresholds: Tuple[float, ...] = (0.75, 0.45, 0.22, 0.10)

    # -- v2 --------------------------------------------------------------
    # Stability is "difficulty of forgetting"; even the most important memory
    # must be able to blur, so the growth is capped.
    max_stability: float = 45.0
    mmr_lambda: float = 0.7
    max_chars: int = 2000
    max_item_chars: int = 500
    mention_budget_per_day: int = 3
    negative_intimacy_threshold: float = 0.7
    # "strict" forbids a recall from restoring detail beyond fidelity.
    fidelity_policy: str = "strict"
    # Hybrid retrieval candidate window (per route).
    candidate_limit: int = 50

    @property
    def max_level(self) -> int:
        return self.levels - 1

    def weight_sum(self) -> float:
        return sum(self.importance_weights.values()) or 1.0


PRESETS: Dict[str, Profile] = {
    # AI companion: emotions and self-relevance dominate, 5 levels.
    "companion": Profile(
        name="companion",
        levels=5,
        importance_weights={
            "llm": 0.30,
            "emotion": 0.18,
            "substance": 0.18,
            "self": 0.13,
            "repetition": 0.09,
            "novelty": 0.04,
            "explicit": 0.08,
        },
        retrieval_weights={
            # Hybrid candidates need relevance to dominate; salient-but-
            # unrelated memories otherwise crowd out the thing being asked
            # about (measured on the 283-turn replay).
            "similarity": 0.50,
            "retrievability": 0.15,
            "importance": 0.15,
            "recency": 0.05,
            "relationship": 0.10,
            "emotion": 0.05,
        },
        enable_relationship=True,
        gist_style="warm",
    ),
    # Support desk: facts and repetition (repeat = recurring problem), 3 levels.
    "support": Profile(
        name="support",
        levels=3,
        importance_weights={
            "llm": 0.35,
            "substance": 0.20,
            "emotion": 0.02,
            "self": 0.08,
            "repetition": 0.25,
            "novelty": 0.05,
            "explicit": 0.05,
        },
        retrieval_weights={
            "similarity": 0.45,
            "retrievability": 0.15,
            "importance": 0.25,
            "recency": 0.15,
            "relationship": 0.00,
            "emotion": 0.00,
        },
        cooldown_hours=1.0,
        gist_style="factual",
    ),
    # Learning: novelty matters, retain an "impression" but no emotional residue.
    "education": Profile(
        name="education",
        levels=4,
        importance_weights={
            "llm": 0.30,
            "substance": 0.15,
            "emotion": 0.02,
            "self": 0.12,
            "repetition": 0.13,
            "novelty": 0.20,
            "explicit": 0.08,
        },
        retrieval_weights={
            "similarity": 0.40,
            "retrievability": 0.20,
            "importance": 0.20,
            "recency": 0.10,
            "relationship": 0.05,
            "emotion": 0.05,
        },
        cooldown_hours=6.0,
        gist_style="factual",
    ),
    # Enterprise knowledge: almost no forgetting experience.
    "knowledge": Profile(
        name="knowledge",
        levels=2,
        importance_weights={
            "llm": 0.40,
            "substance": 0.25,
            "emotion": 0.00,
            "self": 0.05,
            "repetition": 0.10,
            "novelty": 0.15,
            "explicit": 0.05,
        },
        retrieval_weights={
            "similarity": 0.60,
            "retrievability": 0.10,
            "importance": 0.20,
            "recency": 0.10,
            "relationship": 0.00,
            "emotion": 0.00,
        },
        cooldown_hours=0.0,
        idle_ratio=0.01,
        gist_style="factual",
        level_thresholds=(0.05,),
    ),
}

DEFAULT_PROFILE = "companion"


def get_profile(name: str) -> Profile:
    try:
        return PRESETS[name]
    except KeyError:
        raise KeyError(
            "unknown profile {!r}; available: {}".format(name, ", ".join(sorted(PRESETS)))
        )


def load_profiles() -> Tuple[str, ...]:
    return tuple(sorted(PRESETS))
