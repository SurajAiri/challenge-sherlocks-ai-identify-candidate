"""
Belief Engine ("Inference Engine (Beliefs)" in the diagram).

Owns the only code path allowed to write `logit_candidate` /
`logit_not_candidate` / `probability_*` on a ParticipantState. Reads
happen elsewhere freely (read-only view); writes are centralized here
so there's exactly one place that defines what "the belief" means.

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
"""
from __future__ import annotations

import math

from engine.core.detection_state import DetectionState, DetectionStateTracker
from engine.core.schemas import NormalizedEvidence
from engine.core.state_store import ParticipantState, ParticipantStateRepository

# Clamp accumulated logits so one long session with lots of one-sided
# evidence can't overflow math.exp or make old evidence irreversible -
# a strong late signal should always still be able to move the needle.
LOGIT_CLAMP = 12.0

# A participant this far into "not candidate" territory is treated as
# effectively eliminated for display/ranking purposes, though its
# state is never discarded (a correction later in the call, e.g. an
# interviewer briefly answering a technical aside, should still be
# able to pull it back).
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
        state.logit_candidate = max(
            -LOGIT_CLAMP, min(LOGIT_CLAMP, state.logit_candidate + normalized.delta_candidate_logit)
        )
        state.logit_not_candidate = max(
            -LOGIT_CLAMP,
            min(LOGIT_CLAMP, state.logit_not_candidate + normalized.delta_not_candidate_logit),
        )
        state.record_evidence(normalized.evidence)
        state.last_updated_t = normalized.evidence.t
        self.recompute_probabilities(repository)

    def recompute_probabilities(self, repository: ParticipantStateRepository) -> None:
        """Refresh `probability_candidate` / `probability_not_candidate`
        on every participant from current logits. Cheap (O(participants)),
        safe to call after every single evidence update - interview
        calls top out at a handful of participants."""
        logits = {pid: p.logit_candidate for pid, p in repository.participants.items()}
        probs = softmax(logits)
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
