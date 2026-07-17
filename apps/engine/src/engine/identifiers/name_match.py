"""
NameMatchIdentifier.

The obvious first signal: does this participant's display name look
like the candidate's name (from calendar/context), or like one of the
known interviewers? Deliberately kept WEAK relative to behavioral
signals - the demo_clean reference scenario exists specifically to
punish anything that treats name matching as authoritative: the
candidate joins as "MacBook Pro" (zero string overlap with their real
name) while a silent observer joins as "iPhone" (an equally
device-like name, easily confused for the same pattern). Name matching
is real evidence, just not sufficient evidence on its own - it's one
weak signal among several, which is the whole design point of this
engine.

Runs BOTH one_time (at join, when we first see a name) and continuous
(on participant_update, e.g. the "sorry, I'm Suraj" mid-call rename) -
a name can become informative at any point in the call, not just at
the start.
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

# Below this similarity ratio we don't treat it as a match at all -
# short/common name fragments would otherwise generate noise.
MATCH_THRESHOLD = 0.72

# Interviewer-name matches are weighted stronger than candidate-name
# matches: a participant claiming to literally BE a named interviewer
# is stronger negative evidence than a generic name-similarity is
# positive evidence, because interviewer_names is closed/authoritative
# context data, whereas "may share the candidate's name" is naturally
# fuzzier (nicknames, mistyped names, homonyms).
CANDIDATE_MATCH_STRENGTH_SCALE = 0.6
INTERVIEWER_MATCH_STRENGTH_SCALE = 0.85


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


class NameMatchIdentifier(Identifier):
    id = "name_match"
    weight = 0.9
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

        candidate_sim = _similarity(display_name, session.candidate_name)
        if candidate_sim >= MATCH_THRESHOLD:
            await self.emit(
                ctx,
                participant_id=participant_id,
                signal="candidate_name_match",
                direction="for_candidate",
                strength=candidate_sim * CANDIDATE_MATCH_STRENGTH_SCALE,
                reasoning=(
                    f"Display name '{display_name}' closely matches the expected "
                    f"candidate name '{session.candidate_name}' "
                    f"(similarity {candidate_sim:.2f})."
                ),
                t=t,
            )

        best_interviewer_sim = 0.0
        best_interviewer_name = ""
        for interviewer_name in session.interviewer_names:
            sim = _similarity(display_name, interviewer_name)
            if sim > best_interviewer_sim:
                best_interviewer_sim = sim
                best_interviewer_name = interviewer_name

        if best_interviewer_sim >= MATCH_THRESHOLD:
            await self.emit(
                ctx,
                participant_id=participant_id,
                signal="interviewer_name_match",
                direction="against_candidate",
                strength=best_interviewer_sim * INTERVIEWER_MATCH_STRENGTH_SCALE,
                reasoning=(
                    f"Display name '{display_name}' closely matches known "
                    f"interviewer '{best_interviewer_name}' "
                    f"(similarity {best_interviewer_sim:.2f})."
                ),
                t=t,
            )
