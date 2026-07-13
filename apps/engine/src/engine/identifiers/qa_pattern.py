"""
QuestionAnsweringPatternIdentifier.

Temporal, continuous, transcript-driven. This is the identifier most
directly aimed at the reference scenario's "primary" expected evidence:
"p_mbp is the participant being asked questions by both interviewers;
p_mbp never asks an interview-style question of anyone else."

Heuristic (intentionally simple - a real system would swap this for an
LLM classification pass or a trained interaction-role classifier, this
is the base-layer version): a transcript segment ending in "?" is
treated as a question. The speaker of a question is weak evidence
AGAINST being the candidate (interviewers ask questions). The next
speaker after a question - answering, i.e. NOT asking another question
back - is weak evidence FOR being the candidate.

This is genuinely weak on its own (candidates ask clarifying questions
too; interviewers occasionally make statements) which is exactly why
it's one signal among several rather than a rule. Its value is in
correlating with the other identifiers: in the reference scenario it
agrees with speaking_share and disagrees with name_match, which is the
kind of cross-signal corroboration multi-signal fusion is for.
"""
from __future__ import annotations

from engine.core.identifiers.base import Identifier, IdentifierContext, IdentifierKind, IdentifierRunMode
from engine.core.schemas import SimEvent, SimEventType


def _looks_like_question(text: str) -> bool:
    stripped = text.strip()
    return stripped.endswith("?")


class QuestionAnsweringPatternIdentifier(Identifier):
    id = "qa_pattern"
    weight = 0.6
    kind = IdentifierKind.TEMPORAL
    run_mode = IdentifierRunMode.CONTINUOUS
    listens_to = frozenset({SimEventType.TRANSCRIPT_SEGMENT.value})

    def __init__(self) -> None:
        # Per-session scratch state: was the *previous* transcript
        # segment (from any speaker) a question, and if so, who asked
        # it? Deliberately kept on the identifier instance rather than
        # in the shared state store - it's private working memory for
        # this one heuristic, not something any other identifier or
        # the dashboard needs to see.
        self._last_question_by: str | None = None

    async def on_event(self, event: SimEvent, ctx: IdentifierContext) -> None:
        if event.participant_id is None:
            return
        text = event.data.get("text")
        if not isinstance(text, str) or not text.strip():
            return

        is_question = _looks_like_question(text)

        if is_question:
            snippet = text.strip()
            snippet = f"{snippet[:60]}..." if len(snippet) > 60 else snippet
            await self.emit(
                ctx,
                participant_id=event.participant_id,
                signal="asked_question",
                direction="against_candidate",
                strength=0.35,
                reasoning=(
                    f"Asked a question ('{snippet}') - interview-style questioning "
                    "is typically interviewer behavior."
                ),
                t=event.t,
            )
        elif self._last_question_by is not None and self._last_question_by != event.participant_id:
            await self.emit(
                ctx,
                participant_id=event.participant_id,
                signal="answered_question",
                direction="for_candidate",
                strength=0.45,
                reasoning="Responded (not with another question) immediately after another participant asked a question - consistent with being the person being interviewed.",
                t=event.t,
            )

        self._last_question_by = event.participant_id if is_question else None
