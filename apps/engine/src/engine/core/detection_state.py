"""
Detection State Machine.

Tracks the session's overall identification *stage* - not a new belief
signal, a read of the belief we already have. Deliberately does not
introduce a parallel scoring system: it reuses the exact thresholds
`output_formatter.py` already uses to decide what to tell the
dashboard (INSUFFICIENT_EVIDENCE_THRESHOLD / CONFIDENT_THRESHOLD /
AMBIGUITY_MARGIN), so "what state are we in" and "what did we just
tell the client" can never silently disagree.

States:

  SEARCHING          - no participant clears INSUFFICIENT_EVIDENCE_THRESHOLD.
                        Includes session start AND a new participant
                        joining before they've accumulated any evidence -
                        both are "don't know who yet", not different
                        states. A new joiner mid-call doesn't need its
                        own state; if they turn out to be a stronger
                        hypothesis, they'll organically pull the
                        softmax-normalized leader back down through
                        LIKELY/SEARCHING, because probability_candidate
                        is recomputed across the whole pool on every
                        update - no special-casing required here.
  LIKELY_CANDIDATE   - someone clears the insufficient-evidence floor
                        but isn't yet a clean, unambiguous leader.
  STABLE_CANDIDATE   - a leader clears CONFIDENT_THRESHOLD with no one
                        else within AMBIGUITY_MARGIN, held for
                        STABLE_ENTRY_STREAK consecutive snapshots.
  LOST_CANDIDATE     - was STABLE, leader dropped below
                        STABLE_EXIT_THRESHOLD. Transitional by design:
                        the very next snapshot re-derives state fresh
                        from wherever the number actually lands
                        (LIKELY or SEARCHING) - LOST is a one-message
                        signal ("we just lost confidence"), not a
                        resting state the Scheduler parks in
                        indefinitely.

Hysteresis (separate enter/exit thresholds + a streak requirement)
exists specifically so a leader oscillating right at the boundary
(e.g. 0.54 / 0.56 / 0.54 across successive messages, common with noisy
borderline evidence) doesn't flap the state every message - that would
defeat the entire point of the Scheduler tiers this drives, causing
tier-thrashing instead of the compute savings we're after.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from engine.core.output_formatter import (
    AMBIGUITY_MARGIN,
    CONFIDENT_THRESHOLD,
    INSUFFICIENT_EVIDENCE_THRESHOLD,
)
from engine.core.state_store import ParticipantState

# Exit bar is deliberately lower than the entry bar (CONFIDENT_THRESHOLD) -
# this is the hysteresis band. Without a gap, any noise sitting exactly
# on CONFIDENT_THRESHOLD flips state every message.
STABLE_EXIT_THRESHOLD = 0.45

# Consecutive qualifying snapshots required before entering STABLE.
# Requires the lead to be *sustained*, not a single lucky message, so
# the Scheduler doesn't downshift on a one-off strong-but-noisy signal.
STABLE_ENTRY_STREAK = 2

assert STABLE_EXIT_THRESHOLD < CONFIDENT_THRESHOLD, "hysteresis band must be non-empty"


class DetectionState(str, Enum):
    SEARCHING = "searching"
    LIKELY_CANDIDATE = "likely_candidate"
    STABLE_CANDIDATE = "stable_candidate"
    LOST_CANDIDATE = "lost_candidate"


@dataclass
class DetectionStateTracker:
    """One instance per session (lives on the repository/engine, not
    per-participant - this is a statement about the *session's*
    identification progress, not about any one person)."""

    state: DetectionState = DetectionState.SEARCHING
    _stable_streak: int = field(default=0, repr=False)

    def update(self, participants: list[ParticipantState]) -> DetectionState:
        """Recompute state from the current belief snapshot. Call this
        right after `BeliefEngine.recompute_probabilities()` - it's the
        one place that already has the full, freshly-normalized pool."""
        previous = self.state

        if not participants:
            self._stable_streak = 0
            self.state = DetectionState.SEARCHING
            return self.state

        ranked = sorted(participants, key=lambda p: p.probability_candidate, reverse=True)
        top = ranked[0]
        runner_up = ranked[1] if len(ranked) > 1 else None
        margin_clear = runner_up is None or (
            top.probability_candidate - runner_up.probability_candidate > AMBIGUITY_MARGIN
        )

        qualifies_for_stable = top.probability_candidate >= CONFIDENT_THRESHOLD and margin_clear

        if qualifies_for_stable:
            self._stable_streak += 1
        else:
            self._stable_streak = 0

        if previous == DetectionState.STABLE_CANDIDATE:
            # Only fall out below the (lower) exit bar - the hysteresis
            # gap itself is what prevents flapping right at the entry
            # threshold.
            if top.probability_candidate < STABLE_EXIT_THRESHOLD or not margin_clear:
                self.state = DetectionState.LOST_CANDIDATE
            else:
                self.state = DetectionState.STABLE_CANDIDATE
        elif previous == DetectionState.LOST_CANDIDATE:
            # Transitional: re-derive fresh from current numbers rather
            # than staying LOST indefinitely.
            self.state = self._derive_fresh(top, qualifies_for_stable)
        else:
            self.state = self._derive_fresh(top, qualifies_for_stable)

        return self.state

    def _derive_fresh(self, top: ParticipantState, qualifies_for_stable: bool) -> DetectionState:
        if qualifies_for_stable and self._stable_streak >= STABLE_ENTRY_STREAK:
            return DetectionState.STABLE_CANDIDATE
        if top.probability_candidate >= INSUFFICIENT_EVIDENCE_THRESHOLD:
            return DetectionState.LIKELY_CANDIDATE
        return DetectionState.SEARCHING
