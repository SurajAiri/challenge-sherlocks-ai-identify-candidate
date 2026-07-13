"""
ScreenshareHeuristicIdentifier.

Instant, continuous, deliberately low-weight. Candidates in technical
interviews frequently share their screen to walk through code;
interviewers occasionally do too (sharing a problem statement, an IDE
template), so this signal alone proves very little - it's included as
a small corroborating nudge, not a decisive one, and is weighted well
below name_match/speaking_share/qa_pattern accordingly.
"""
from __future__ import annotations

from engine.core.identifiers.base import Identifier, IdentifierContext, IdentifierKind, IdentifierRunMode
from engine.core.schemas import SimEvent, SimEventType


class ScreenshareHeuristicIdentifier(Identifier):
    id = "screenshare_heuristic"
    weight = 0.25
    kind = IdentifierKind.INSTANT
    run_mode = IdentifierRunMode.CONTINUOUS
    listens_to = frozenset({SimEventType.SCREENSHARE_START.value})

    async def on_event(self, event: SimEvent, ctx: IdentifierContext) -> None:
        if event.participant_id is None:
            return
        await self.emit(
            ctx,
            participant_id=event.participant_id,
            signal="screenshare_start",
            direction="for_candidate",
            strength=0.4,
            reasoning="Started sharing their screen - mildly consistent with walking an interviewer through a solution, though interviewers share screens too.",
            t=event.t,
        )
