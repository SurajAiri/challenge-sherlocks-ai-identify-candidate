"""
Regression tests for `output_formatter._select_possible_candidates`.

Bug this guards against: early in a session, noise (a decoy
participant, a one-off spurious identifier match) can transiently
outscore the real candidate before the real candidate has accumulated
enough evidence to "settle". The old logic anchored its ambiguity band
to `AMBIGUITY_MARGIN` below whichever participant currently topped the
ranking - so a noisy leader could silently exclude a real candidate
who was above the "worth mentioning" floor but just outside that
leader's tight margin, and (worse) a single noisy snapshot clearing
CONFIDENT_THRESHOLD could collapse the output to that one wrong id.
Since downstream fraud analysis only investigates ids present in
`possible_candidate_ids`, either failure mode is equivalent to
wrongly clearing the real candidate - which must never happen.

These tests exercise `_select_possible_candidates` directly with
hand-built `ParticipantState`s rather than driving a full scenario
through real identifiers, since the property under test is about the
selection function's behavior given a probability snapshot, not about
any particular identifier's scoring.
"""

from __future__ import annotations

from engine.core.detection_state import DetectionState
from engine.core.output_formatter import _select_possible_candidates
from engine.core.state_store import ParticipantState


def _participant(pid: str, probability_candidate: float) -> ParticipantState:
    state = ParticipantState(participant_id=pid)
    state.probability_candidate = probability_candidate
    return state


def test_real_candidate_not_dropped_by_transient_noisy_leader():
    """Noise spikes ahead early on, but the real candidate has also
    cleared the insufficient-evidence floor. Not yet STABLE_CANDIDATE
    (this hasn't been sustained), so both should be reported - the
    real candidate must never be silently excluded just because it's
    outside the noisy leader's tight ambiguity margin."""
    participants = sorted(
        [
            _participant("p_noise_decoy", 0.60),
            _participant("p_real_candidate", 0.40),
            _participant("p_interviewer", 0.05),
        ],
        key=lambda p: p.probability_candidate,
        reverse=True,
    )

    result = _select_possible_candidates(participants, DetectionState.LIKELY_CANDIDATE)

    assert "p_real_candidate" in result
    assert "p_noise_decoy" in result
    assert "p_interviewer" not in result


def test_single_noisy_snapshot_does_not_collapse_to_one_id():
    """A leader alone clearing CONFIDENT_THRESHOLD on a single snapshot
    (detection_state not yet STABLE_CANDIDATE) must not narrow the
    output to just that one id - the sustained-lead requirement in
    detection_state.py exists precisely so a one-off spike doesn't get
    reported as a confident, single-candidate result."""
    participants = sorted(
        [
            _participant("p_noise_decoy", 0.60),
            _participant("p_real_candidate", 0.40),
        ],
        key=lambda p: p.probability_candidate,
        reverse=True,
    )

    result = _select_possible_candidates(participants, DetectionState.LIKELY_CANDIDATE)

    assert result != ["p_noise_decoy"]
    assert "p_real_candidate" in result


def test_collapses_to_single_id_once_genuinely_stable():
    """Once detection_state has confirmed a durable, sustained lead
    (STABLE_CANDIDATE - which already required clearing
    CONFIDENT_THRESHOLD by AMBIGUITY_MARGIN for STABLE_ENTRY_STREAK
    consecutive snapshots), it's safe to report a single id."""
    participants = sorted(
        [
            _participant("p_real_candidate", 0.75),
            _participant("p_noise_decoy", 0.10),
            _participant("p_interviewer", 0.05),
        ],
        key=lambda p: p.probability_candidate,
        reverse=True,
    )

    result = _select_possible_candidates(participants, DetectionState.STABLE_CANDIDATE)

    assert result == ["p_real_candidate"]


def test_below_insufficient_evidence_floor_is_still_excluded():
    """Not a regression case - a participant who hasn't cleared the
    minimum "worth mentioning" floor at all should still be excluded,
    even under the widened not-yet-stable band. This isn't about
    protecting a real candidate; there's honestly not enough evidence
    for them yet."""
    participants = sorted(
        [
            _participant("p_noise_decoy", 0.60),
            _participant("p_real_candidate", 0.20),
        ],
        key=lambda p: p.probability_candidate,
        reverse=True,
    )

    result = _select_possible_candidates(participants, DetectionState.SEARCHING)

    assert "p_real_candidate" not in result
    assert result == ["p_noise_decoy"]


def test_no_participants_returns_empty():
    assert _select_possible_candidates([], DetectionState.SEARCHING) == []


def test_top_below_floor_returns_empty_even_if_state_stable():
    """Defensive: STABLE_CANDIDATE should be unreachable with a
    below-floor top given how detection_state.py derives state, but
    the formatter shouldn't rely on that invariant holding elsewhere -
    it re-checks the floor itself regardless of detection_state."""
    participants = [_participant("p_a", 0.10), _participant("p_b", 0.05)]

    result = _select_possible_candidates(participants, DetectionState.STABLE_CANDIDATE)

    assert result == []
