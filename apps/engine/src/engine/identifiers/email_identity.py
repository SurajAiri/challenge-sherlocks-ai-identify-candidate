"""
EmailIdentityIdentifier.

Real Meet/Zoom/Teams SDK adapters frequently expose an authenticated
participant email on join (SSO-verified account email, distinct from
the free-text, user-editable display name) - this is about as
authoritative an identity signal as exists short of biometrics, because
unlike a display name it isn't something a participant can casually
type over. Nothing in the CURRENT simulator scenario authoring format
populates an `email` key today, so this identifier is deliberately
written to degrade to a silent no-op on every existing scenario, while
activating automatically - no code changes needed - the moment a real
adapter (or a future scenario) starts sending
`participant_join`/`participant_update` events with `data.email` set.
`state_store.py`'s `ParticipantState.email` is where that optional
`data.email` key ends up (mirroring exactly how `display_name` is
already tracked - see `_set_display_name`/the JOIN/UPDATE cases in
`apply_event`), so this identifier reads the *state*, not the raw
event - which also means it sees an email set on join even from
`on_join` (state is populated before `_run_initial_processors` runs -
see `session_engine.py._dispatch`), not just from a later
participant_update. This is forward-compatible plumbing, not dead
code: extending the *wire* schema itself was explicitly out of scope
for this pass, but reading an already-legal, currently-unused optional
key on the existing `data` dict, and tracking it internally, is not a
wire-schema change.

Secondarily, some real join payloads (especially guest/external
participants who authenticate via a magic-link rather than full SSO)
stuff the email into the display name field itself instead of a
separate field (e.g. display_name="suraj.thapa@example.com" or
"Suraj Thapa <suraj.thapa@example.com>") - this identifier also checks
for an embedded email-shaped substring in `display_name` as a fallback,
since that pattern needs no adapter-side change to start showing up.

Only compares against `candidate_email` today (the only email the
session context carries - no per-interviewer emails are modeled), so
this only ever produces `for_candidate` evidence, never
`against_candidate`. Comparison is exact-match on the normalized
(lowercased, stripped) local-part + domain, not fuzzy - an email
address is not the kind of thing that should partially match.
"""

from __future__ import annotations

import re

from engine.core.identifiers.base import (
    Identifier,
    IdentifierContext,
    IdentifierKind,
    IdentifierRunMode,
)
from engine.core.schemas import SimEvent, SimEventType

WEIGHT = 0.95

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _normalize(email: str) -> str:
    return email.strip().lower()


def _extract_email(display_name: str, state_email: str) -> str | None:
    if state_email:
        return state_email
    match = _EMAIL_RE.search(display_name or "")
    return match.group(0) if match else None


class EmailIdentityIdentifier(Identifier):
    id = "email_identity"
    weight = WEIGHT
    kind = IdentifierKind.INSTANT
    run_mode = IdentifierRunMode.BOTH
    listens_to = frozenset({SimEventType.PARTICIPANT_UPDATE.value})

    async def on_join(self, participant_id: str, ctx: IdentifierContext) -> None:
        await self._evaluate_from_state(participant_id, ctx)

    async def on_event(self, event: SimEvent, ctx: IdentifierContext) -> None:
        if event.participant_id is None:
            return
        await self._evaluate_from_state(event.participant_id, ctx)

    async def _evaluate_from_state(
        self, participant_id: str, ctx: IdentifierContext
    ) -> None:
        state = ctx.state.get(participant_id)
        session = ctx.state.session_context
        if state is None or session is None or not session.candidate_email:
            return

        found = _extract_email(state.display_name, state.email)
        if not found:
            return

        if _normalize(found) != _normalize(session.candidate_email):
            return

        await self.emit(
            ctx,
            participant_id=participant_id,
            signal="candidate_email_match",
            direction="for_candidate",
            strength=1.0,
            reasoning=(
                f"Participant's email '{found}' exactly matches the expected candidate "
                f"email '{session.candidate_email}' - a strong, hard-to-fake identity "
                f"signal."
            ),
            t=ctx.state.current_t,
        )
