"""
LLMNameRoleIdentifier.

`name_match.py`'s fuzzy string-similarity approach is fast and free,
but it's genuinely blind to the cases display names show up in real
calls: nicknames ("Suraj" vs "Suraj Thapa"), transliteration/romanization
differences, a contractor suffix ("Suraj T. (Contractor)"), an
interviewer who fat-fingered the invite title, or a candidate joining
under a completely unrelated handle picked up from their OS/device
account. A pure edit-distance ratio either matches too eagerly (short
common fragments) or misses these entirely - there's no threshold that
gets both "MacBook Pro" (correctly, zero overlap - fine) and "S. Thapa
(personal)" (should match, but low character-level similarity to
"Suraj Thapa") right at once.

This identifier asks an LLM to reason about the *semantics* of the
match instead of raw string overlap: given the observed display name
plus the session's known-authoritative context (candidate name/email,
interviewer names, calendar invite title/organizer), classify whether
this name plausibly belongs to the candidate, an interviewer, or is
unclear/unrelated - with its own confidence and short reasoning.

Deliberately still just ONE weak signal, same philosophy as
`name_match`: kept at a comparable weight, not layered as an
override. `name_match` stays registered unchanged - on any LLM
failure (no API key, timeout, bad response) this identifier emits
nothing for that tick, and the cheap deterministic signal is still
there to fall back on. The two are complementary, not a
replacement/upgrade relationship in code.

Runs BOTH one_time (at join) and continuous (on participant_update) -
identical trigger shape to `name_match`, since a name can become
informative (or newly misleading) at any point in the call.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from engine.core.identifiers.base import (
    Identifier,
    IdentifierContext,
    IdentifierKind,
    IdentifierRunMode,
)
from engine.core.llm_client import structured_completion
from engine.core.schemas import SimEvent, SimEventType

WEIGHT = 0.75


class NameRoleVerdict(BaseModel):
    verdict: Literal["candidate", "interviewer", "unclear"] = Field(
        description="Best guess at whose display name this is, given the session context."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="0..1 confidence in this verdict.")
    reasoning: str = Field(description="One short sentence explaining the verdict.")


SYSTEM_PROMPT = (
    "You help identify who is who in a live job-interview video call. "
    "You will be given one participant's display name and the session's "
    "authoritative context (expected candidate name/email, known interviewer "
    "names, calendar invite details). Decide whether this display name "
    "plausibly belongs to the CANDIDATE being interviewed, to one of the "
    "known INTERVIEWERS, or is UNCLEAR (e.g. a device name, a nickname you "
    "can't confidently place, or an unrelated observer). Consider nicknames, "
    "romanization/transliteration differences, honorifics, and suffixes like "
    "'(Contractor)' or '(Guest)'. Do not assume a name is the candidate just "
    "because it doesn't match any interviewer - 'unclear' is the correct "
    "answer when you genuinely can't tell. Respond ONLY with the requested "
    "JSON."
)


def _build_user_prompt(
    display_name: str,
    candidate_name: str,
    candidate_email: str,
    interviewer_names: list[str],
    calendar_invite: dict,
) -> str:
    return (
        f"Display name to classify: {display_name!r}\n"
        f"Expected candidate name: {candidate_name!r}\n"
        f"Expected candidate email: {candidate_email!r}\n"
        f"Known interviewer names: {interviewer_names!r}\n"
        f"Calendar invite: {calendar_invite!r}\n"
    )


class LLMNameRoleIdentifier(Identifier):
    id = "llm_name_role"
    weight = WEIGHT
    kind = IdentifierKind.INSTANT
    run_mode = IdentifierRunMode.BOTH
    listens_to = frozenset({SimEventType.PARTICIPANT_UPDATE.value})

    async def on_join(self, participant_id: str, ctx: IdentifierContext) -> None:
        state = ctx.state.get(participant_id)
        if state is None or not state.display_name:
            return
        await self._evaluate(participant_id, state.display_name, state.joined_at or 0.0, ctx)

    async def on_event(self, event: SimEvent, ctx: IdentifierContext) -> None:
        if event.participant_id is None:
            return
        name = event.data.get("display_name")
        if not isinstance(name, str) or not name:
            return
        await self._evaluate(event.participant_id, name, event.t, ctx)

    async def _evaluate(self, participant_id: str, display_name: str, t: float, ctx: IdentifierContext) -> None:
        session = ctx.state.session_context
        if session is None:
            return

        user_prompt = _build_user_prompt(
            display_name=display_name,
            candidate_name=session.candidate_name,
            candidate_email=session.candidate_email,
            interviewer_names=session.interviewer_names,
            calendar_invite=session.calendar_invite,
        )

        verdict = await structured_completion(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            schema=NameRoleVerdict,
        )
        if verdict is None or verdict.verdict == "unclear" or verdict.confidence <= 0.0:
            return

        if verdict.verdict == "candidate":
            await self.emit(
                ctx,
                participant_id=participant_id,
                signal="llm_candidate_name_match",
                direction="for_candidate",
                strength=verdict.confidence,
                reasoning=(
                    f"LLM name assessment: display name '{display_name}' plausibly the candidate "
                    f"({verdict.confidence:.2f} confidence) - {verdict.reasoning}"
                ),
                t=t,
            )
        else:  # interviewer
            await self.emit(
                ctx,
                participant_id=participant_id,
                signal="llm_interviewer_name_match",
                direction="against_candidate",
                strength=verdict.confidence,
                reasoning=(
                    f"LLM name assessment: display name '{display_name}' plausibly an interviewer "
                    f"({verdict.confidence:.2f} confidence) - {verdict.reasoning}"
                ),
                t=t,
            )
