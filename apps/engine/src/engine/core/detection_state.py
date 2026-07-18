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

  EXPLORING          - mandatory warmup: the session hasn't yet
                        accumulated enough *and enough kinds of*
                        evidence for any prediction to be meaningful.
                        Three independent gates - elapsed time,
                        evidence volume, and evidence diversity - all
                        adaptive to how many participants are in the
                        call, must be satisfied before the machine is
                        allowed to advance. The engine reports
                        possible_candidate_ids=[] during this phase -
                        not because it doesn't know, but because it
                        explicitly refuses to rush a guess on thin
                        data.

                        Deliberately NOT gated on probability_candidate
                        itself (see BeliefEngine's
                        NO_EVIDENCE_BASELINE_LOGIT docstring) - a
                        confidence-based exit would let one lucky early
                        signal spike a softmax over a small pool and
                        skip warmup entirely, which is precisely the
                        failure mode this gate exists to prevent. The
                        gate only ever asks "have we sampled enough,
                        and from enough independent sources" - never
                        "does the current leader look confident."
  SEARCHING          - warmup cleared, but no participant clears
                        INSUFFICIENT_EVIDENCE_THRESHOLD yet.
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

# ---------------------------------------------------------------------------
# EXPLORING warmup gate
# ---------------------------------------------------------------------------
# THREE conditions must all be true before the state machine is allowed
# to advance past EXPLORING. They are intentionally independent so any
# one of them can act as a floor on its own - each catches a failure
# mode the other two don't:
#
#   elapsed time      - raw session time (simulation clock, not wall
#     clock). Even if identifiers fire very fast on the first events,
#     we don't name anyone until at least this many seconds of session
#     content have been observed.
#
#   evidence volume    - total evidence log entries summed across ALL
#     participants. Measures "have enough independent observations
#     landed?" regardless of elapsed time - a slow session with sparse
#     events would otherwise clear the time gate while still sitting on
#     near-zero evidence.
#
#   evidence diversity - evidence volume alone is gameable: the same
#     cheap, noisy identifier firing 3x on one participant clears the
#     old flat MIN_EVIDENCE_PIECES=3 floor without a single independent
#     source agreeing. This requires evidence to have arrived from at
#     least MIN_DISTINCT_IDENTIFIERS different identifiers (summed
#     across participants) before the volume count is trusted.
#
# All three are ADAPTIVE to how many participants are on the call, via
# `_participant_scaled_floor` below. Rationale: the softmax the belief
# engine runs is a competition across every current participant - more
# candidates in the pool means more ways for early evidence to be
# ambiguous or misleading, so a 2-person call and an 8-person "silent
# observers" call should not be held to the same fixed bar. Both the
# per-participant scaling factor and the caps are deliberately modest:
# this is still a hard, cheap, pre-belief gate, not a second scoring
# system - see module docstring.
MIN_ELAPSED_SECONDS_BASE: float = 20.0
SECONDS_PER_EXTRA_PARTICIPANT: float = 5.0
MAX_MIN_ELAPSED_SECONDS: float = 45.0

MIN_EVIDENCE_PIECES_BASE: int = 3
EVIDENCE_PIECES_PER_EXTRA_PARTICIPANT: int = 1
MAX_MIN_EVIDENCE_PIECES: int = 10

# Participant count at/below which no scaling is applied - a normal
# 1:1 or 1-candidate-2-interviewers call gets exactly the old fixed
# floor; scaling only kicks in for larger rooms.
BASELINE_PARTICIPANTS_FOR_SCALING: int = 2

# Independent identifiers (not repeat firings of the same one) that
# must have contributed evidence, summed across all participants,
# before EXPLORING can clear. Not scaled by participant count - this
# is a floor on *kinds* of signal, which a bigger room doesn't inherently
# increase.
MIN_DISTINCT_IDENTIFIERS: int = 2


def _participant_scaled_floor(
    participant_count: int, base: float, per_extra: float, cap: float
) -> float:
    """`base` for BASELINE_PARTICIPANTS_FOR_SCALING or fewer people on
    the call; +`per_extra` for every participant beyond that, capped at
    `cap` so a very large room (e.g. a farm of silent observers) can't
    push the warmup floor out indefinitely."""
    extra = max(0, participant_count - BASELINE_PARTICIPANTS_FOR_SCALING)
    return min(cap, base + extra * per_extra)


class DetectionState(str, Enum):
    EXPLORING = "exploring"
    SEARCHING = "searching"
    LIKELY_CANDIDATE = "likely_candidate"
    STABLE_CANDIDATE = "stable_candidate"
    LOST_CANDIDATE = "lost_candidate"


@dataclass
class DetectionStateTracker:
    """One instance per session (lives on the repository/engine, not
    per-participant - this is a statement about the *session's*
    identification progress, not about any one person)."""

    state: DetectionState = DetectionState.EXPLORING
    _stable_streak: int = field(default=0, repr=False)

    def update(
        self,
        participants: list[ParticipantState],
        elapsed_t: float = 0.0,
    ) -> DetectionState:
        """Recompute state from the current belief snapshot. Call this
        right after `BeliefEngine.recompute_probabilities()` - it's the
        one place that already has the full, freshly-normalized pool.

        `elapsed_t` is the simulation clock's current_t (seconds of
        session content seen so far). It's checked against the
        (participant-count-scaled) elapsed-time floor as part of the
        EXPLORING gate. Callers that don't have it available can omit
        it; the time gate will simply never clear until they pass a
        non-zero value.
        """
        previous = self.state

        # ---- EXPLORING gate ------------------------------------------
        # Stay in EXPLORING until all three adaptive floors (time,
        # evidence volume, evidence diversity) are cleared. Once we
        # leave EXPLORING we never re-enter it (even if evidence later
        # evaporates through decay - that's what LOST_CANDIDATE is
        # for). The gate is one-way by design.
        if previous == DetectionState.EXPLORING:
            participant_count = len(participants)
            total_evidence = sum(len(p.evidence_log) for p in participants)
            # Distinct identifiers that have ever contributed to ANY
            # participant, not evidence_log entries - a participant can
            # have several evidence_log rows from the same identifier
            # re-firing, which should not count as diverse signal.
            distinct_identifiers: set[str] = set()
            for p in participants:
                distinct_identifiers.update(p.identifier_contributions.keys())

            min_elapsed = _participant_scaled_floor(
                participant_count,
                MIN_ELAPSED_SECONDS_BASE,
                SECONDS_PER_EXTRA_PARTICIPANT,
                MAX_MIN_ELAPSED_SECONDS,
            )
            min_evidence = _participant_scaled_floor(
                participant_count,
                MIN_EVIDENCE_PIECES_BASE,
                EVIDENCE_PIECES_PER_EXTRA_PARTICIPANT,
                MAX_MIN_EVIDENCE_PIECES,
            )

            time_ok = elapsed_t >= min_elapsed
            evidence_ok = total_evidence >= min_evidence
            diversity_ok = len(distinct_identifiers) >= MIN_DISTINCT_IDENTIFIERS
            if not (time_ok and evidence_ok and diversity_ok):
                # Still warming up - remain in EXPLORING, reset streak
                # so the first real snapshot after we emerge doesn't
                # inherit a stale streak count.
                self._stable_streak = 0
                return self.state
            # Both gates cleared: fall through to normal state derivation
            # from the current belief snapshot.

        # ---- Normal state machine ------------------------------------
        if not participants:
            self._stable_streak = 0
            self.state = DetectionState.SEARCHING
            return self.state

        ranked = sorted(
            participants, key=lambda p: p.probability_candidate, reverse=True
        )
        top = ranked[0]
        runner_up = ranked[1] if len(ranked) > 1 else None
        margin_clear = runner_up is None or (
            top.probability_candidate - runner_up.probability_candidate
            > AMBIGUITY_MARGIN
        )

        qualifies_for_stable = (
            top.probability_candidate >= CONFIDENT_THRESHOLD and margin_clear
        )

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
            # EXPLORING (just cleared), SEARCHING, or LIKELY_CANDIDATE
            self.state = self._derive_fresh(top, qualifies_for_stable)

        return self.state

    def _derive_fresh(
        self, top: ParticipantState, qualifies_for_stable: bool
    ) -> DetectionState:
        if qualifies_for_stable and self._stable_streak >= STABLE_ENTRY_STREAK:
            return DetectionState.STABLE_CANDIDATE
        if top.probability_candidate >= INSUFFICIENT_EVIDENCE_THRESHOLD:
            return DetectionState.LIKELY_CANDIDATE
        return DetectionState.SEARCHING
