"""
Output Formatter.

Turns the current ParticipantStateRepository snapshot into the
outbound `EngineMessage` the dashboard renders. Ranking is by
`probability_candidate` (the normalized, competing-hypotheses track);
`probability_not_candidate` rides along per-candidate for the
dashboard/evaluator to use as an elimination signal, per the "we store
two probabilities" note.

`candidate_participant_id` is only populated once the top candidate
clears MIN_REPORTING_CONFIDENCE - below that, the honest answer is "not
sure yet", and reporting a low-confidence guess as if it were an
answer is worse than reporting no answer (see requirements: "Gracefully
handle uncertainty instead of making incorrect assumptions").
"""
from __future__ import annotations

from engine.core.schemas import EngineCandidateOut, EngineMessage
from engine.core.state_store import ParticipantState, ParticipantStateRepository

MIN_REPORTING_CONFIDENCE = 0.35
MAX_EVIDENCE_IN_OUTPUT = 4


def _reasoning_trail(state: ParticipantState) -> list[str]:
    return [e.reasoning for e in state.evidence_log[-MAX_EVIDENCE_IN_OUTPUT:]]


def _summarize(state: ParticipantState) -> str:
    if not state.evidence_log:
        return "No evidence observed for this participant yet."
    latest = state.evidence_log[-1]
    return latest.reasoning


def format_message(repository: ParticipantStateRepository) -> EngineMessage:
    participants = sorted(
        repository.participants.values(),
        key=lambda p: p.probability_candidate,
        reverse=True,
    )

    top_candidates = [
        EngineCandidateOut(
            participant_id=p.participant_id,
            display_name=p.display_name or None,
            confidence=round(p.probability_candidate, 4),
            probability_not_candidate=round(p.probability_not_candidate, 4),
            reasoning=_summarize(p),
            evidence=_reasoning_trail(p),
        )
        for p in participants
    ]

    top = participants[0] if participants else None
    reported_top = top if (top is not None and top.probability_candidate >= MIN_REPORTING_CONFIDENCE) else None

    return EngineMessage(
        type="prediction",
        t=repository.current_t,
        candidate_participant_id=reported_top.participant_id if reported_top else None,
        confidence=round(reported_top.probability_candidate, 4) if reported_top else None,
        reasoning=_summarize(reported_top) if reported_top else "Not enough evidence yet to identify the candidate.",
        top_candidates=top_candidates,
    )
