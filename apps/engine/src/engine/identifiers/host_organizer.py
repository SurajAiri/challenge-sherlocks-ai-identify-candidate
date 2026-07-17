"""
HostOrganizerExclusionIdentifier.

Handles a specific, high-confidence case the generic `name_match`/
`llm_name_role` identifiers don't specifically target: the meeting's
calendar organizer is present. In practice, whoever organized/scheduled
the interview invite is essentially always an interviewer (or a
recruiter/scheduler), never the candidate - nobody schedules their own
interview as the organizer of record. This also helps with "interviewer
enters the wrong candidate name": even if `context.candidate_name` is
wrong or stale, `calendar_invite.organizer` is separate, independently-
sourced context, so a match against it doesn't depend on
`candidate_name` being correct at all.

Deliberately its own identifier rather than folded into `name_match`:
the evidence direction/strength here isn't about "does this look like
the candidate's name", it's a completely different claim ("this
specific role - meeting organizer - is essentially never the
candidate"), and keeping it separate means its weight can be tuned
independently and its firing shows up as its own line in the
reasoning trail ("matches calendar organizer") rather than folding
into a generic name-match explanation.

Reuses the same fuzzy string-similarity helper/threshold as
`name_match.py` rather than reinventing one - this is still a plain
string-identity claim ("is this literally the organizer's name"), not
a semantic one, so the LLM identifiers aren't a better fit here.

Runs BOTH (join + participant_update), same trigger shape as the other
name-based identifiers - the organizer could reveal their real name
after joining under a nickname too.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from engine.core.identifiers.base import (
    Identifier,
    IdentifierContext,
    IdentifierKind,
    IdentifierRunMode,
)
from engine.core.schemas import SimEvent, SimEventType

MATCH_THRESHOLD = 0.72

# Kept high, comparable to interviewer_name_match in name_match.py -
# "you are literally the person who organized this interview" is about
# as strong a non-candidate signal as a rule-based identifier can offer.
MATCH_STRENGTH_SCALE = 0.9


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


class HostOrganizerExclusionIdentifier(Identifier):
    id = "host_organizer_exclusion"
    weight = 0.85
    kind = IdentifierKind.INSTANT
    run_mode = IdentifierRunMode.BOTH
    listens_to = frozenset({SimEventType.PARTICIPANT_UPDATE.value})

    async def on_join(self, participant_id: str, ctx: IdentifierContext) -> None:
        state = ctx.state.get(participant_id)
        if state is None or not state.display_name:
            return
        await self._evaluate(
            participant_id, state.display_name, state.joined_at or 0.0, ctx
        )

    async def on_event(self, event: SimEvent, ctx: IdentifierContext) -> None:
        if event.participant_id is None:
            return
        name = event.data.get("display_name")
        if not isinstance(name, str) or not name:
            return
        await self._evaluate(event.participant_id, name, event.t, ctx)

    async def _evaluate(
        self, participant_id: str, display_name: str, t: float, ctx: IdentifierContext
    ) -> None:
        session = ctx.state.session_context
        if session is None:
            return

        organizer = session.calendar_invite.get("organizer")
        if not isinstance(organizer, str) or not organizer:
            return

        sim = _similarity(display_name, organizer)
        if sim < MATCH_THRESHOLD:
            return

        await self.emit(
            ctx,
            participant_id=participant_id,
            signal="calendar_organizer_match",
            direction="against_candidate",
            strength=sim * MATCH_STRENGTH_SCALE,
            reasoning=(
                f"Display name '{display_name}' matches the calendar invite's "
                f"organizer '{organizer}' (similarity {sim:.2f}) - meeting organizers "
                f"are essentially never the candidate being interviewed."
            ),
            t=t,
        )
