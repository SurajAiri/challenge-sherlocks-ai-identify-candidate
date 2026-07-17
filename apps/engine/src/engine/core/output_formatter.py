"""
Output Formatter.

Turns the current ParticipantStateRepository snapshot into the outbound
`EngineMessage` the dashboard / anti-fraud router consumes. Ranking is
by `probability_candidate` (the normalized, competing-hypotheses
track); `probability_not_candidate` rides along independently, per the
"we store two probabilities" note.

`possible_candidate_ids` is deliberately NOT always length 1. The
system may not skip/misname the real candidate, so instead of forcing
a single guess whenever evidence is thin, three states are possible:

  - []                    - not enough evidence yet ("insufficient_evidence")
  - [single_id]           - one participant clearly leads ("confident")
  - [id, id, ...]         - top few are within AMBIGUITY_MARGIN of each
                            other ("ambiguous") - report all of them
                            rather than arbitrarily picking one.

`evidence` (the reasoning trail) is only populated for ids that made it
into `possible_candidate_ids` - explainability matters for whoever is
actually being named, not for every participant on every message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from engine.core.schemas import EngineMessage
from engine.core.state_store import ParticipantState, ParticipantStateRepository

if TYPE_CHECKING:
    # detection_state.py imports the three threshold constants below
    # from this module at *its* module-load time, so this module must
    # not import DetectionState back at module-load time in turn - see
    # format_message's local import for the runtime side of this.
    from engine.core.detection_state import DetectionState

# Top candidate must clear this before we say anything at all - below
# it, the honest answer is "not sure yet", not a low-confidence guess.
INSUFFICIENT_EVIDENCE_THRESHOLD = 0.35

# Top candidate must ALSO clear this higher bar, and lead everyone else
# by at least AMBIGUITY_MARGIN, before we report a single confident id.
# This is what stops one weak signal in a small call (e.g. 2 people,
# uniform-prior baseline already 0.5) from being reported as a
# confident pick - see the ambiguity-margin band below.
CONFIDENT_THRESHOLD = 0.55
AMBIGUITY_MARGIN = 0.15

MAX_POSSIBLE_CANDIDATES = 3
MAX_EVIDENCE_IN_OUTPUT = 4


def _evidence_trail(state: ParticipantState) -> list[str]:
    return [e.reasoning for e in state.evidence_log[-MAX_EVIDENCE_IN_OUTPUT:]]


def _select_possible_candidates(participants: list[ParticipantState]) -> list[str]:
    if not participants:
        return []

    top = participants[0]
    if top.probability_candidate < INSUFFICIENT_EVIDENCE_THRESHOLD:
        return []

    # Everyone within AMBIGUITY_MARGIN of the leader is "in contention".
    band = [
        p
        for p in participants
        if top.probability_candidate - p.probability_candidate <= AMBIGUITY_MARGIN
    ]

    if len(band) == 1 and top.probability_candidate >= CONFIDENT_THRESHOLD:
        return [top.participant_id]

    return [p.participant_id for p in band[:MAX_POSSIBLE_CANDIDATES]]


def format_message(
    repository: ParticipantStateRepository,
    detection_state: Optional["DetectionState"] = None,
) -> EngineMessage:
    from engine.core.detection_state import DetectionState as _DetectionState

    if detection_state is None:
        detection_state = _DetectionState.SEARCHING

    participants = sorted(
        repository.participants.values(),
        key=lambda p: p.probability_candidate,
        reverse=True,
    )

    probability_being_candidate = [
        (p.participant_id, round(p.probability_candidate, 4)) for p in participants
    ]
    probability_not_being_candidate = [
        (p.participant_id, round(p.probability_not_candidate, 4)) for p in participants
    ]

    possible_candidate_ids = _select_possible_candidates(participants)

    by_id = {p.participant_id: p for p in participants}
    evidence = {pid: _evidence_trail(by_id[pid]) for pid in possible_candidate_ids}

    return EngineMessage(
        type="prediction",
        t=repository.current_t,
        possible_candidate_ids=possible_candidate_ids,
        probability_being_candidate=probability_being_candidate,
        probability_not_being_candidate=probability_not_being_candidate,
        evidence=evidence,
        detection_state=detection_state.value,
    )
