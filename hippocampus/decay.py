"""Plasticity and forgetting.

FSRS-shaped rather than plain exponential decay:

    R(t, S) = (1 + F * t / S) ** DECAY          retrievability
    S'      = S * (1 + K * (1 - R) * I)         stability growth on recall

``level`` (how clearly it surfaces) and ``fidelity`` (how much detail is still
stored) are separate. Recall may improve accessibility, but it can never bring
back detail that graded forgetting already discarded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from .models import Engram

FACTOR = 19.0 / 81.0
DECAY = -0.5

# R thresholds that push a trace down one level, from L0 towards L4.
LEVEL_THRESHOLDS = (0.75, 0.45, 0.22, 0.10)

# How much a successful recall grows stability.
GROWTH = 2.0

# Stability floor for a memory nobody cares about (roughly 2 hours).
S_MIN = 0.02

# How strongly importance stretches stability. exp(11*I) => 1x .. 60000x.
IMPORTANCE_GAIN = 11.0

# Default ceiling. Profiles may override; ``reinforce`` must respect it.
S_MAX = 45.0


def retrievability(active_age_days: float, stability: float) -> float:
    """Probability the memory can be recalled right now."""
    if stability <= 0:
        return 0.0
    t = max(0.0, active_age_days)
    return (1.0 + FACTOR * t / stability) ** DECAY


def reinforce(
    stability: float,
    r: float,
    importance: float,
    max_stability: float = S_MAX,
) -> float:
    """Spacing effect: harder recall + higher importance -> bigger jump."""
    difficulty_weight = max(0.0, 1.0 - r)
    gain = 1.0 + GROWTH * difficulty_weight * (0.5 + importance)
    grown = max(stability, stability * gain)
    return min(max(1e-6, max_stability), grown)


def initial_stability(importance: float, max_stability: float = S_MAX) -> float:
    """Important things start out much harder to forget."""
    raw = S_MIN * pow(2.718281828459045, IMPORTANCE_GAIN * max(0.0, min(1.0, importance)))
    return min(max_stability, raw)


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
    """Which level the trace should be at, given its retrievability."""
    level = 0
    for i, threshold in enumerate(thresholds):
        if r < threshold:
            level = i + 1
    level = min(level, floor_level(importance, max_level))
    return max(0, min(level, max_level))


def level_down(level: int, fidelity: int, steps: int = 1) -> int:
    """Recall sharpens accessibility, never beyond stored fidelity."""
    return max(fidelity, level - steps)


def tick_engram(engram: "Engram", factor: float, now, profile) -> Tuple[int, int]:
    """Apply one day of aging at ``factor`` (1.0 active, idle_ratio idle).

    Returns (old_level, old_fidelity) so callers can count changes.
    """
    old_level, old_fidelity = engram.level, engram.fidelity
    natural_age = max(0.0, (now - engram.created_at).total_seconds() / 86400.0)
    # A trace cannot be older than its own existence. The cap must NOT be
    # multiplied by `factor`: an idle day would then clamp an already-aged
    # memory *down*, resurrecting it.
    cap = natural_age
    engram.active_age_days = min(engram.active_age_days + factor, cap)

    r = retrievability(engram.active_age_days, engram.stability)
    target = target_level(
        r, engram.importance, profile.max_level, profile.level_thresholds
    )

    # Fidelity is monotonic: detail only goes away.
    if target > engram.fidelity:
        engram.fidelity = target
    if engram.fidelity > engram.level:
        engram.level = engram.fidelity
    if target > engram.level:
        engram.level = target

    return old_level, old_fidelity
