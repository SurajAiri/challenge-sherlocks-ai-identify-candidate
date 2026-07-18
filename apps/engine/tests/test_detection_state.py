"""
Unit tests for the EXPLORING warmup gate in detection_state.py.

Focus: the gate is adaptive (scales with participant count) and
diversity-aware (volume of evidence isn't enough on its own - it has
to come from more than one identifier). See detection_state.py's
module docstring for why this is deliberately NOT confidence-based.
"""

from __future__ import annotations

from engine.core.detection_state import (
    BASELINE_PARTICIPANTS_FOR_SCALING,
    MIN_DISTINCT_IDENTIFIERS,
    MIN_ELAPSED_SECONDS_BASE,
    MIN_EVIDENCE_PIECES_BASE,
    SECONDS_PER_EXTRA_PARTICIPANT,
    DetectionState,
    DetectionStateTracker,
)
from engine.core.state_store import IdentifierContribution, ParticipantState


def _participant_with_evidence(
    pid: str, identifier_hits: dict[str, int]
) -> ParticipantState:
    """Build a ParticipantState with `identifier_hits[identifier_id]`
    evidence_log entries attributed to that identifier, and a matching
    identifier_contributions bucket per identifier (mirrors what
    BeliefEngine.apply() does for real evidence)."""
    state = ParticipantState(participant_id=pid)
    for identifier_id, count in identifier_hits.items():
        state.identifier_contributions[identifier_id] = IdentifierContribution()
        for _ in range(count):
            state.evidence_log.append(_fake_evidence(pid, identifier_id))
    return state


def _fake_evidence(pid: str, identifier_id: str):
    from engine.core.schemas import Evidence

    return Evidence(
        participant_id=pid,
        identifier_id=identifier_id,
        t=0.0,
        signal="test_signal",
        direction="for_candidate",
        strength=0.5,
        reasoning="test",
    )


def test_two_person_call_uses_base_floor_unscaled():
    """At/below BASELINE_PARTICIPANTS_FOR_SCALING, the floor is exactly
    the old fixed constants - no scaling kicks in."""
    tracker = DetectionStateTracker()
    participants = [
        _participant_with_evidence("p_a", {"id_1": 2, "id_2": 1}),
        _participant_with_evidence("p_b", {}),
    ]
    assert len(participants) <= BASELINE_PARTICIPANTS_FOR_SCALING

    # Evidence (3) and diversity (2 identifiers) both clear, but time
    # doesn't yet - must still be EXPLORING.
    state = tracker.update(participants, elapsed_t=MIN_ELAPSED_SECONDS_BASE - 1)
    assert state == DetectionState.EXPLORING

    # Now time clears too - base floor is fully satisfied.
    state = tracker.update(participants, elapsed_t=MIN_ELAPSED_SECONDS_BASE)
    assert state != DetectionState.EXPLORING


def test_single_identifier_repeated_is_not_diverse_enough():
    """Volume alone (even well above MIN_EVIDENCE_PIECES_BASE) must not
    clear the gate if it all came from one identifier - this is the
    exact gameable case the diversity gate was added to close."""
    tracker = DetectionStateTracker()
    participants = [
        _participant_with_evidence("p_a", {"id_1": 10}),
        _participant_with_evidence("p_b", {}),
    ]

    state = tracker.update(participants, elapsed_t=MIN_ELAPSED_SECONDS_BASE + 10)

    assert state == DetectionState.EXPLORING
    assert MIN_DISTINCT_IDENTIFIERS > 1  # sanity: gate is actually active


def test_larger_room_requires_more_evidence_and_time_than_base():
    """A room with several extra participants beyond the baseline
    should NOT clear the gate on exactly the base-floor evidence/time
    that a 2-person call would clear on."""
    tracker = DetectionStateTracker()
    extra_participants = 4
    participant_count = BASELINE_PARTICIPANTS_FOR_SCALING + extra_participants

    participants = [
        _participant_with_evidence(f"p_{i}", {"id_1": 1, "id_2": 1})
        for i in range(participant_count)
    ]
    # Exactly base evidence total (2 per participant just for diversity,
    # but total volume kept at the base minimum) and base elapsed time.
    total_pieces = sum(len(p.evidence_log) for p in participants)
    assert (
        total_pieces >= MIN_EVIDENCE_PIECES_BASE
    )  # base alone would pass on a small room

    state = tracker.update(participants, elapsed_t=MIN_ELAPSED_SECONDS_BASE)

    # Scaled floor for elapsed time should exceed the base for this
    # many extra participants, so time_ok is False even though it
    # would have cleared the unscaled base floor.
    scaled_time_floor = (
        MIN_ELAPSED_SECONDS_BASE + extra_participants * SECONDS_PER_EXTRA_PARTICIPANT
    )
    assert scaled_time_floor > MIN_ELAPSED_SECONDS_BASE
    assert state == DetectionState.EXPLORING


def test_gate_is_one_way_even_after_scaled_floor_clears():
    """Once EXPLORING clears via the scaled gate, later shrinking the
    participant list (e.g. someone leaves) must not re-trigger it -
    the gate only runs `if previous == EXPLORING`."""
    tracker = DetectionStateTracker()
    participants = [
        _participant_with_evidence("p_a", {"id_1": 2, "id_2": 2}),
        _participant_with_evidence("p_b", {}),
    ]
    state = tracker.update(participants, elapsed_t=MIN_ELAPSED_SECONDS_BASE + 1)
    assert state != DetectionState.EXPLORING

    # Fewer participants, zero elapsed time passed to this call - if
    # the gate re-ran, this would look like it should still be
    # EXPLORING. It must not re-enter.
    state = tracker.update([participants[0]], elapsed_t=0.0)
    assert state != DetectionState.EXPLORING
