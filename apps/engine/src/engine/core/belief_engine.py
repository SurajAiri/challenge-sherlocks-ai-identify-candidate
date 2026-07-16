"""
Belief Engine ("Inference Engine (Beliefs)" in the diagram).

Owns the only code path allowed to write `identifier_contributions` /
`logit_candidate` / `logit_not_candidate` / `probability_*` on a
ParticipantState. Reads happen elsewhere freely (read-only view);
writes are centralized here so there's exactly one place that defines
what "the belief" means.

Two tracks, updated independently by every NormalizedEvidence:

  - logit_candidate: accumulated log-odds of "this participant IS the
    candidate". Converted to a probability via softmax ACROSS all
    currently-known participants, not an independent sigmoid - because
    "is the candidate" is inherently a competition between the people
    in the room (probabilities should redistribute as one person looks
    more likely and the field of remaining suspects shrinks).
  - logit_not_candidate: accumulated log-odds of "this participant is
    clearly NOT the candidate" (e.g. strong interviewer-name match).
    Converted to a probability via an independent sigmoid, NOT
    normalized against other participants - multiple people can
    simultaneously be "almost certainly not the candidate", and that's
    the whole point: it's an elimination signal, used to shrink the
    search space (e.g. skip expensive identifiers for participants
    already at >0.9 not-candidate), not a competing hypothesis.

Per-identifier decay
---------------------
Both tracks used to be one flat float each, mutated in place by
`+=` on every NormalizedEvidence - which meant an individual
identifier's contribution was gone the instant it was applied, folded
permanently into one number. That made "reduce the influence of stale
evidence over time" structurally impossible without redoing this.

Now each track is the SUM, computed fresh on every recompute, of one
IdentifierContribution per identifier that's ever fired for this
participant (see state_store.py) - each carrying its own raw
(undecayed) running sub-total, when it was last touched, and its
identifier's own configured `decay_half_life`. `apply()` still just
adds a delta into a bucket, same as before; the only new work happens
in `recompute_probabilities`, which sums each bucket's *decayed* value
- `raw * 0.5 ** (elapsed / half_life)` - rather than summing raw
values directly. `decay_half_life=None` (the default on every
identifier unless explicitly set) means that bucket's decay factor is
always 1.0, i.e. exactly today's behavior, unchanged.

This is deliberately lazy/read-time rather than a background process
ticking every participant down on a timer: elapsed time is computed
fresh from `last_touched_t` on whichever call triggers a recompute
next, whether that's new evidence arriving (`apply()`) or the periodic
heartbeat that already exists to keep the dashboard fresh through
quiet stretches (see session_engine.py) - decay only becomes visible
through a recompute either way, so hooking both call sites is what
makes decay show up promptly on fresh evidence AND drift visibly
during silence, without a third timer of its own.
"""
from __future__ import annotations

import math

from engine.core.detection_state import DetectionState, DetectionStateTracker
from engine.core.schemas import NormalizedEvidence
from engine.core.state_store import IdentifierContribution, ParticipantState, ParticipantStateRepository

# Clamp accumulated logits so one long session with lots of one-sided
# evidence can't overflow math.exp or make old evidence irreversible -
# a strong late signal should always still be able to move the needle.
# Applied twice: once per-identifier-bucket (in `apply`, so a single
# relentless identifier can't dominate purely by out-accumulating
# everyone else) and once on the combined decayed sum (in
# `recompute_probabilities`, same reason the old single accumulator
# clamped there).
LOGIT_CLAMP = 12.0

# A participant this far into "not candidate" territory is treated as
# effectively eliminated for display/ranking purposes, though its
# state is never discarded (a correction later in the call, e.g. an
# interviewer briefly answering a technical aside, should still be
# able to pull it back - or, for a decaying elimination identifier,
# simply enough time passing with nothing to reinforce it; a
# non-decaying one stays sticky, by that identifier's own choice).
NOT_CANDIDATE_ELIMINATION_THRESHOLD = 0.9


def sigmoid(x: float) -> float:
    x = max(-LOGIT_CLAMP, min(LOGIT_CLAMP, x))
    return 1.0 / (1.0 + math.exp(-x))


def softmax(logits: dict[str, float]) -> dict[str, float]:
    if not logits:
        return {}
    clamped = {k: max(-LOGIT_CLAMP, min(LOGIT_CLAMP, v)) for k, v in logits.items()}
    m = max(clamped.values())
    exps = {k: math.exp(v - m) for k, v in clamped.items()}
    total = sum(exps.values()) or 1.0
    return {k: v / total for k, v in exps.items()}


def decay_factor(elapsed: float, half_life: float | None) -> float:
    """1.0 (no decay) if half_life is None/non-positive or elapsed is
    non-positive (nothing to decay yet); standard exponential
    half-life decay otherwise. Pulled out as its own function because
    it's the one piece of actual decay math and is worth being able to
    unit-test/tune in isolation from the accumulation logic around it.
    """
    if half_life is None or half_life <= 0 or elapsed <= 0:
        return 1.0
    return 0.5 ** (elapsed / half_life)


class BeliefEngine:
    def __init__(self) -> None:
        # One tracker per session, alongside the belief state itself -
        # detection state is a read of the belief snapshot, recomputed
        # in the same place/cadence as probabilities themselves.
        self.detection_state = DetectionStateTracker()

    def apply(self, repository: ParticipantStateRepository, normalized: NormalizedEvidence) -> None:
        pid = normalized.evidence.participant_id
        if pid is None:
            return  # context-level evidence with no target participant; nothing to update yet
        state, _ = repository.get_or_create(pid, normalized.evidence.t)

        identifier_id = normalized.evidence.identifier_id
        contribution = state.identifier_contributions.get(identifier_id)
        if contribution is None:
            contribution = IdentifierContribution()
            state.identifier_contributions[identifier_id] = contribution

        contribution.candidate_logit = max(
            -LOGIT_CLAMP, min(LOGIT_CLAMP, contribution.candidate_logit + normalized.delta_candidate_logit)
        )
        contribution.not_candidate_logit = max(
            -LOGIT_CLAMP, min(LOGIT_CLAMP, contribution.not_candidate_logit + normalized.delta_not_candidate_logit)
        )
        contribution.last_touched_t = normalized.evidence.t
        # An identifier's decay_half_life is a static class attribute
        # in practice, but stamping it fresh from the NormalizedEvidence
        # every time (rather than trusting whatever was here from the
        # first observation) means a registry change takes effect on
        # this bucket immediately rather than only for identifiers seen
        # again for the first time.
        contribution.decay_half_life = normalized.decay_half_life

        state.record_evidence(normalized.evidence)
        state.last_updated_t = normalized.evidence.t
        self.recompute_probabilities(repository)

    def recompute_probabilities(self, repository: ParticipantStateRepository) -> None:
        """Refresh `probability_candidate` / `probability_not_candidate`
        (and the cached `logit_*` fields they're derived from) on every
        participant, by summing each identifier's *currently decayed*
        contribution. Cheap - O(participants x identifiers-that-have-
        fired-for-them), which tops out small for an interview call -
        and safe to call from either `apply()` (new evidence) or a bare
        heartbeat (time passing with no new evidence, so decay is
        actually visible), both of which pass the same
        `repository.current_t` as "now"."""
        t_now = repository.current_t

        candidate_logits: dict[str, float] = {}
        for pid, state in repository.participants.items():
            candidate_total = 0.0
            not_candidate_total = 0.0
            for contribution in state.identifier_contributions.values():
                elapsed = t_now - contribution.last_touched_t
                factor = decay_factor(elapsed, contribution.decay_half_life)
                candidate_total += contribution.candidate_logit * factor
                not_candidate_total += contribution.not_candidate_logit * factor

            state.logit_candidate = max(-LOGIT_CLAMP, min(LOGIT_CLAMP, candidate_total))
            state.logit_not_candidate = max(-LOGIT_CLAMP, min(LOGIT_CLAMP, not_candidate_total))
            candidate_logits[pid] = state.logit_candidate

        probs = softmax(candidate_logits)
        for pid, state in repository.participants.items():
            state.probability_candidate = probs.get(pid, 0.0)
            state.probability_not_candidate = sigmoid(state.logit_not_candidate)

        # Detection state is derived from the same fresh softmax pool,
        # not scored independently - see detection_state.py docstring.
        self.detection_state.update(list(repository.participants.values()))

    @staticmethod
    def is_eliminated(state: ParticipantState) -> bool:
        return state.probability_not_candidate >= NOT_CANDIDATE_ELIMINATION_THRESHOLD

    @property
    def current_detection_state(self) -> DetectionState:
        return self.detection_state.state
