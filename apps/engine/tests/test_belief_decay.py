"""
Tests for the Belief Engine's per-identifier decay (see
core/belief_engine.py's module docstring for the design). Existing
belief behavior (no identifier sets decay_half_life) is already
covered, unchanged, by test_session_engine.py - these tests are
specifically about the new axis: decay_factor's math in isolation, a
decaying identifier's contribution actually shrinking over elapsed
time, a non-decaying (default) identifier staying exactly as sticky as
before, and recompute_probabilities being safely callable with no new
evidence (the heartbeat path).
"""

from __future__ import annotations

import pytest

from engine.core.belief_engine import BeliefEngine, decay_factor
from engine.core.evidence_normalizer import normalize
from engine.core.schemas import Evidence
from engine.core.state_store import ParticipantStateRepository


def test_decay_factor_no_half_life_is_always_full_strength():
    assert decay_factor(elapsed=10_000, half_life=None) == 1.0
    assert decay_factor(elapsed=10_000, half_life=0) == 1.0
    assert decay_factor(elapsed=10_000, half_life=-5) == 1.0


def test_decay_factor_halves_at_exactly_one_half_life():
    assert decay_factor(elapsed=30, half_life=30) == 0.5


def test_decay_factor_never_negative_elapsed_edge_case():
    # A contribution read before it was ever touched (elapsed <= 0)
    # is full strength, not amplified.
    assert decay_factor(elapsed=0, half_life=30) == 1.0
    assert decay_factor(elapsed=-5, half_life=30) == 1.0


def _evidence(
    t: float, participant_id: str, identifier_id: str, strength: float = 1.0
) -> Evidence:
    return Evidence(
        identifier_id=identifier_id,
        participant_id=participant_id,
        signal="test_signal",
        direction="for_candidate",
        strength=strength,
        reasoning="test",
        t=t,
    )


def test_decaying_identifier_contribution_shrinks_with_elapsed_time_no_new_evidence():
    repo = ParticipantStateRepository()
    engine = BeliefEngine()

    normalized = normalize(
        _evidence(t=0, participant_id="p_a", identifier_id="decaying"),
        1.0,
        decay_half_life=10,
    )
    engine.apply(repo, normalized)
    state = repo.participants["p_a"]
    logit_at_t0 = state.logit_candidate
    assert logit_at_t0 > 0

    # No new evidence - just time passing, driven the same way
    # heartbeat() drives it: bump current_t, recompute.
    repo.current_t = 10  # exactly one half-life later
    engine.recompute_probabilities(repo)
    assert state.logit_candidate == pytest.approx(logit_at_t0 * 0.5)

    repo.current_t = 40  # three more half-lives later (four total)
    engine.recompute_probabilities(repo)
    assert state.logit_candidate == pytest.approx(logit_at_t0 * 0.0625)


def test_non_decaying_identifier_contribution_never_shrinks():
    repo = ParticipantStateRepository()
    engine = BeliefEngine()

    normalized = normalize(
        _evidence(t=0, participant_id="p_a", identifier_id="sticky"),
        1.0,
        decay_half_life=None,
    )
    engine.apply(repo, normalized)
    state = repo.participants["p_a"]
    logit_at_t0 = state.logit_candidate

    repo.current_t = 100_000
    engine.recompute_probabilities(repo)
    assert state.logit_candidate == pytest.approx(logit_at_t0)


def test_two_identifiers_decay_independently():
    """The whole point: one identifier's evidence going stale must not
    touch another identifier's contribution for the same participant."""
    repo = ParticipantStateRepository()
    engine = BeliefEngine()

    fast = normalize(
        _evidence(t=0, participant_id="p_a", identifier_id="fast_decay"),
        1.0,
        decay_half_life=5,
    )
    slow = normalize(
        _evidence(t=0, participant_id="p_a", identifier_id="no_decay"),
        1.0,
        decay_half_life=None,
    )
    engine.apply(repo, fast)
    engine.apply(repo, slow)
    state = repo.participants["p_a"]

    fast_contribution = state.identifier_contributions["fast_decay"].candidate_logit
    slow_contribution = state.identifier_contributions["no_decay"].candidate_logit

    repo.current_t = 25  # five half-lives for the fast one
    engine.recompute_probabilities(repo)

    # Raw per-identifier buckets are untouched by decay - only the
    # summed, cached logit_candidate reflects the decayed view.
    assert state.identifier_contributions[
        "fast_decay"
    ].candidate_logit == pytest.approx(fast_contribution)
    assert state.identifier_contributions["no_decay"].candidate_logit == pytest.approx(
        slow_contribution
    )

    expected_total = fast_contribution * decay_factor(
        25, 5
    ) + slow_contribution * decay_factor(25, None)
    assert state.logit_candidate == pytest.approx(expected_total)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
