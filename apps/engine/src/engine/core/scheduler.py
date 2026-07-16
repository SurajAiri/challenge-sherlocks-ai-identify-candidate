"""
Scheduler.

Deliberately NOT a poller. `session_engine.py` is reactive on purpose
(see its module docstring) - real interview events are sparse enough
that fixed-interval polling would either lag behind speech or waste
cycles, and that reasoning doesn't change just because we now want
tiered compute.

What the Scheduler actually gates is narrower and compatible with the
reactive core: given an event has already arrived and a CONTINUOUS
identifier is about to be invoked for it, should this *specific*
identifier be allowed to run right now, or is it too soon since its
last run for the active SchedulingTier?

Cheap identifiers (name_match, qa_pattern, ...) don't declare tiered
intervals at all and are therefore never throttled - only identifiers
that opt in (expensive ones: CV/audio ML, future work per
architecture.md) pay any scheduling cost. This keeps the fast, cheap
majority of identifiers exactly as low-latency as they are today.

Tier is driven by DetectionState, not derived independently - once we
know who the candidate likely is, expensive identifiers can safely run
less often; while still searching, we want maximum signal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from engine.core.detection_state import DetectionState


class SchedulingTier(str, Enum):
    AGGRESSIVE = "aggressive"  # SEARCHING - cast the widest net, we know nothing yet
    BALANCED = "balanced"  # LIKELY_CANDIDATE, LOST_CANDIDATE - narrowing/re-checking
    CONSERVATIVE = "conservative"  # STABLE_CANDIDATE - identity settled, confirm cheaply


TIER_BY_STATE: dict[DetectionState, SchedulingTier] = {
    DetectionState.SEARCHING: SchedulingTier.AGGRESSIVE,
    DetectionState.LIKELY_CANDIDATE: SchedulingTier.BALANCED,
    DetectionState.LOST_CANDIDATE: SchedulingTier.BALANCED,
    DetectionState.STABLE_CANDIDATE: SchedulingTier.CONSERVATIVE,
}


@dataclass
class Scheduler:
    """Tracks last-run time per (identifier_id, participant_id) and
    answers "may this identifier run now". One instance per session,
    alongside the DetectionStateTracker it reads from.
    """

    tier: SchedulingTier = SchedulingTier.AGGRESSIVE
    _last_run_t: dict[tuple[str, str], float] = field(default_factory=dict, repr=False)

    def set_tier_from_state(self, state: DetectionState) -> None:
        self.tier = TIER_BY_STATE[state]

    def may_run(
        self,
        identifier_id: str,
        participant_id: str,
        now_t: float,
        min_interval_by_tier: dict[SchedulingTier, float],
    ) -> bool:
        """`min_interval_by_tier` is the identifier's own declared
        cadence (see Identifier.min_interval_by_tier). An identifier
        that declares nothing for the active tier is never throttled -
        opt-in only, by design."""
        min_interval = min_interval_by_tier.get(self.tier, 0.0)
        if min_interval <= 0.0:
            return True

        key = (identifier_id, participant_id)
        last_t = self._last_run_t.get(key)
        if last_t is None or (now_t - last_t) >= min_interval:
            return True
        return False

    def record_run(self, identifier_id: str, participant_id: str, now_t: float) -> None:
        self._last_run_t[(identifier_id, participant_id)] = now_t
