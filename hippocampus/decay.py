"""Plasticity and forgetting.

FSRS-shaped rather than plain exponential decay:

    R(t, S) = (1 + F * t / S) ** DECAY          retrievability
    S'      = S * (1 + K * (1 - R) * I)         stability growth on recall

Why: `exp(-t/tau)` only says "older is fainter". It cannot express
"the more often it comes up, the harder it is to forget" -- which is what
plasticity actually means. FSRS gives both directions from one model.

Calibration note
----------------
FSRS's default scale assumes a review session every few days. A chat agent
sees a given memory maybe once, so the absolute scale has to be shifted:
`initial_stability` maps importance onto roughly 2 hours .. 12 days, which
makes one-off trivia fade within a month while reinforced memories survive.
These constants were fitted with `eval/replay_30d.py`, not guessed.

S is "difficulty of forgetting". It does not decay when a memory is not
recalled -- R falls, S stays. That asymmetry is the whole point.
"""

from __future__ import annotations

import math
from typing import Tuple

FACTOR = 19.0 / 81.0
DECAY = -0.5

# R thresholds that push a trace down one level, from L0 towards L4.
LEVEL_THRESHOLDS = (0.75, 0.45, 0.22, 0.10)

# How much a successful recall grows stability.
GROWTH = 2.0

# Stability floor for a memory nobody cares about (roughly 2 hours).
S_MIN = 0.02

# How strongly importance stretches stability. exp(11*I) => 1x .. 60000x.
# Steep on purpose: importance is a log prior, stability is exponential in it.
IMPORTANCE_GAIN = 11.0

# Even the most important memory must remain able to blur. Without a ceiling,
# exp(11*I) makes a 0.95 memory effectively permanent.
S_MAX = 45.0


def retrievability(active_age_days: float, stability: float) -> float:
    """Probability the memory can be recalled right now."""
    if stability <= 0:
        return 0.0
    t = max(0.0, active_age_days)
    return (1.0 + FACTOR * t / stability) ** DECAY


def reinforce(stability: float, r: float, importance: float) -> float:
    """Spacing effect: harder recall + higher importance -> bigger jump."""
    difficulty_weight = max(0.0, 1.0 - r)
    gain = 1.0 + GROWTH * difficulty_weight * (0.5 + importance)
    return max(stability, stability * gain)


def initial_stability(importance: float) -> float:
    """Important things start out much harder to forget."""
    raw = S_MIN * math.exp(IMPORTANCE_GAIN * max(0.0, min(1.0, importance)))
    return min(S_MAX, raw)


def floor_level(importance: float, max_level: int) -> int:
    """The most degraded level a trace is ever allowed to reach.

    High-importance memories may blur but never become "it didn't happen".
    """
    if importance >= 0.8:
        floor = 2
    elif importance >= 0.5:
        floor = 3
    else:
        floor = max_level
    return min(floor, max_level)


def target_level(
    r: float,
    importance: float,
    max_level: int,
    thresholds: Tuple[float, ...] = LEVEL_THRESHOLDS,
) -> int:
    """Which level the trace should be at, given its retrievability.

    `thresholds` is per-profile: a knowledge base must not use the same
    curve as a companion.
    """
    level = 0
    for i, threshold in enumerate(thresholds):
        if r < threshold:
            level = i + 1
    level = min(level, floor_level(importance, max_level))
    return max(0, min(level, max_level))


def level_down(level: int, steps: int = 1) -> int:
    """Recall sharpens a memory, but never fully restores it in one go."""
    return max(0, level - steps)
