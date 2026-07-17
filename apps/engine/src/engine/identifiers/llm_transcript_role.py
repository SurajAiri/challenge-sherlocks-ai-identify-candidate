"""
LLMTranscriptRoleIdentifier.

`qa_pattern.py`'s "ends with '?'" heuristic is deliberately naive (see
its own docstring: "a real system would swap this for an LLM
classification pass ... this is the base-layer version"). This
identifier is that upgrade, run alongside `qa_pattern` rather than in
place of it - `qa_pattern` stays cheap, always-on, and immune to LLM
outages; this identifier adds a stronger, semantically-aware corroborating
signal on top, which is exactly the "multiple weak signals, not one
rule" posture the whole engine is built around.

Approach: maintain a small rolling window of the most recent
speaker-labeled transcript segments (per session, kept as identifier-
local scratch state, same pattern `qa_pattern` already uses for
`_last_question_by`). Rather than calling the LLM on every single
segment - expensive, and unnecessary, since role usually doesn't
flip sentence to sentence - this identifier batches: it waits until
enough *new* segments have accumulated (`MIN_NEW_SEGMENTS_BETWEEN_CALLS`)
or enough wall-clock time has passed (`MIN_SECONDS_BETWEEN_CALLS`)
since its last call, whichever comes first, then sends the whole
window to the LLM in one shot and asks it to classify every
participant who appears in that window as interviewee / interviewer /
observer / unclear, with its own confidence and reasoning per person.

This is intentionally its own throttle (plain instance counters), not
the shared Scheduler's per-(identifier, participant) tier mechanism -
that machinery gates "how often may this identifier act on THIS ONE
participant", whereas what we actually want here is "how often may
this identifier make ONE LLM call covering everyone in the window",
which is a session-level cadence, not a per-participant one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from engine.core.identifiers.base import (
    Identifier,
    IdentifierContext,
    IdentifierKind,
    IdentifierRunMode,
)
from engine.core.llm_client import structured_completion
from engine.core.schemas import SimEvent, SimEventType

WEIGHT = 0.65

MAX_WINDOW_SEGMENTS = 12
MIN_NEW_SEGMENTS_BETWEEN_CALLS = 4
MIN_SECONDS_BETWEEN_CALLS = 10.0
# Need at least two distinct speakers in the window for "who's
# interviewing whom" to be a meaningful question at all - a window
# that's just one person monologuing has nothing to contrast.
MIN_DISTINCT_SPEAKERS = 2


@dataclass
class _WindowEntry:
    t: float
    participant_id: str
    display_name: str
    text: str


class ParticipantRoleVerdict(BaseModel):
    participant_id: str = Field(
        description="Must be one of the participant_ids given in the prompt."
    )
    verdict: Literal["interviewee", "interviewer", "observer", "unclear"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(description="One short sentence explaining the verdict.")


class TranscriptRoleAssessment(BaseModel):
    assessments: list[ParticipantRoleVerdict]


SYSTEM_PROMPT = (
    "You analyze a short window of a speaker-labeled interview transcript. "
    "There is exactly one CANDIDATE being interviewed; everyone else is an "
    "INTERVIEWER (asking questions, driving the conversation, evaluating "
    "answers) or an OBSERVER (silent or near-silent, not actually "
    "participating). Based on the conversational behavior in this window "
    "(who asks interview-style questions, who answers at length, who leads "
    "topic transitions, who is addressed by name), classify EVERY "
    "participant_id given as one of: 'interviewee' (this is the candidate "
    "being interviewed), 'interviewer', 'observer', or 'unclear' (genuinely "
    "not enough signal in this window). Do not use participant display "
    "names or any prior assumption about who 'should' be the candidate - "
    "reason only from the observed conversational behavior in this window. "
    "Respond ONLY with the requested JSON, one assessment per participant_id given."
)


def _format_window(window: list[_WindowEntry]) -> str:
    lines = []
    for entry in window:
        snippet = entry.text.strip()
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        lines.append(f"[{entry.participant_id}] {snippet}")
    return "\n".join(lines)


class LLMTranscriptRoleIdentifier(Identifier):
    id = "llm_transcript_role"
    weight = WEIGHT
    kind = IdentifierKind.TEMPORAL
    run_mode = IdentifierRunMode.CONTINUOUS
    listens_to = frozenset({SimEventType.TRANSCRIPT_SEGMENT.value})

    def __init__(self) -> None:
        self._window: list[_WindowEntry] = []
        self._segments_since_last_call: int = 0
        self._last_call_t: float = float("-inf")

    async def on_event(self, event: SimEvent, ctx: IdentifierContext) -> None:
        if event.participant_id is None:
            return
        text = event.data.get("text")
        if not isinstance(text, str) or not text.strip():
            return

        state = ctx.state.get(event.participant_id)
        display_name = state.display_name if state else event.participant_id

        self._window.append(
            _WindowEntry(
                t=event.t,
                participant_id=event.participant_id,
                display_name=display_name,
                text=text,
            )
        )
        if len(self._window) > MAX_WINDOW_SEGMENTS:
            self._window = self._window[-MAX_WINDOW_SEGMENTS:]
        self._segments_since_last_call += 1

        distinct_speakers = {e.participant_id for e in self._window}
        if len(distinct_speakers) < MIN_DISTINCT_SPEAKERS:
            return

        enough_new_segments = (
            self._segments_since_last_call >= MIN_NEW_SEGMENTS_BETWEEN_CALLS
        )
        enough_time_elapsed = (event.t - self._last_call_t) >= MIN_SECONDS_BETWEEN_CALLS
        if not (enough_new_segments or enough_time_elapsed):
            return

        self._segments_since_last_call = 0
        self._last_call_t = event.t

        user_prompt = (
            f"Transcript window ({len(self._window)} most recent segments):\n"
            f"{_format_window(self._window)}\n\n"
            f"participant_ids to assess: {sorted(distinct_speakers)!r}"
        )

        assessment = await structured_completion(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            schema=TranscriptRoleAssessment,
            use_cache=False,  # window content changes every call by construction
        )
        if assessment is None:
            return

        for verdict in assessment.assessments:
            if (
                verdict.participant_id not in distinct_speakers
                or verdict.confidence <= 0.0
            ):
                continue

            if verdict.verdict == "interviewee":
                await self.emit(
                    ctx,
                    participant_id=verdict.participant_id,
                    signal="llm_transcript_interviewee",
                    direction="for_candidate",
                    strength=verdict.confidence,
                    reasoning=(
                        f"LLM transcript-behavior assessment: interviewee-like - "
                        f"{verdict.reasoning}"
                    ),
                    t=event.t,
                )
            elif verdict.verdict in ("interviewer", "observer"):
                # Interviewer is a stronger against-signal than a merely
                # quiet observer being caught up in the same batch call -
                # scale by verdict type, same asymmetry name_match applies
                # between candidate-match/interviewer-match strength.
                scale = 1.0 if verdict.verdict == "interviewer" else 0.6
                await self.emit(
                    ctx,
                    participant_id=verdict.participant_id,
                    signal=f"llm_transcript_{verdict.verdict}",
                    direction="against_candidate",
                    strength=verdict.confidence * scale,
                    reasoning=(
                        f"LLM transcript-behavior assessment: "
                        f"{verdict.verdict}-like - {verdict.reasoning}"
                    ),
                    t=event.t,
                )
            # "unclear" -> no evidence, exactly like name_match's silence
            # on sub-threshold similarity.
