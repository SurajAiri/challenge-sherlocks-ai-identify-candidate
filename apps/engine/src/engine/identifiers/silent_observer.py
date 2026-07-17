"""
SilentObserverIdentifier.

Directly targets the "multiple observers join silently" scenario
requirement: a participant who has been present for a meaningful
amount of time while never speaking, never turning on their webcam,
and never sharing their screen is unlikely to be the person being
interviewed - candidates are, almost by definition, actively
participating (answering questions, presenting solutions). This is
weak evidence AGAINST being the candidate, not proof - a candidate
could plausibly sit through a long interviewer monologue near the
start of a call with their camera off - which is exactly why this
only fires once a meaningful time floor has passed (same discipline
`speaking_share` already applies via `MIN_TOTAL_SECONDS_BEFORE_SIGNAL`)
and strength saturates rather than growing without bound.

TEMPORAL/CONTINUOUS: reassessed on every event (wildcard subscription)
so a long-silent participant's evidence keeps accruing over time even
if the specific event that arrives has nothing to do with them
directly - what matters is elapsed presence, not any one event's
payload.

Throttling note: this identifier evaluates EVERY present participant
on EVERY event, regardless of which participant the triggering event
was actually about (that's the whole point - a silent participant's
"still silent" status needs to keep being reconsidered even while
*other* people are the ones generating events). That means the
Scheduler's built-in per-(identifier, triggering-participant) gate
(`min_interval_by_tier`) is the wrong tool here: it would throttle
based on whoever's event happened to trigger this tick, not based on
the silent participant actually being re-evaluated, so a call with
several active speakers would still re-emit for the silent one on
almost every event. Instead this identifier keeps its own small
per-participant "last emitted at" map (same "private scratch state on
the identifier instance" pattern `qa_pattern` already uses for
`_last_question_by`) and throttles against THAT.

`decay_half_life` is set (unlike most identifiers here, which default
to sticky/permanent): unlike a strong interviewer-name match, silence
is a claim about *ongoing* behavior. A participant silent for the
first 5 minutes who then starts actively answering questions should
have their earlier silent-observer evidence fade rather than
permanently anchor them as "probably not the candidate".
"""

from __future__ import annotations

from engine.core.identifiers.base import (
    Identifier,
    IdentifierContext,
    IdentifierKind,
    IdentifierRunMode,
)
from engine.core.schemas import SimEvent

# Don't judge anyone as "silent" before they've had at least this long
# in the call - the first few seconds after joining are not evidence of
# anything.
MIN_PRESENT_SECONDS_BEFORE_SIGNAL = 45.0

# Strength saturates by this many seconds of total silence-while-present -
# being silent for 5 minutes isn't meaningfully "more" evidence than
# being silent for 3, so this caps rather than growing unbounded.
SATURATION_SECONDS = 180.0

MAX_STRENGTH = 0.55

# Identifier-local throttle: at most one fresh observation per silent
# participant per this many seconds, regardless of how many *other*
# participants' events tick this identifier in between - see module
# docstring on why the shared Scheduler's gate doesn't fit here.
MIN_RECHECK_INTERVAL_SECONDS = 20.0


class SilentObserverIdentifier(Identifier):
    id = "silent_observer"
    weight = 0.5
    kind = IdentifierKind.TEMPORAL
    run_mode = IdentifierRunMode.CONTINUOUS
    listens_to = frozenset({"*"})
    decay_half_life = 120.0

    def __init__(self) -> None:
        self._last_emitted_t: dict[str, float] = {}

    async def on_event(self, event: SimEvent, ctx: IdentifierContext) -> None:
        now_t = ctx.state.current_t
        for state in ctx.state.present():
            if state.joined_at is None:
                continue
            elapsed = now_t - state.joined_at
            if elapsed < MIN_PRESENT_SECONDS_BEFORE_SIGNAL:
                continue

            last_emitted = self._last_emitted_t.get(state.participant_id)
            if (
                last_emitted is not None
                and (now_t - last_emitted) < MIN_RECHECK_INTERVAL_SECONDS
            ):
                continue

            has_ever_participated = (
                state.total_speaking_seconds > 0.0
                or state.webcam_on
                or state.screenshare_on
                or state.total_transcript_words > 0
            )
            if has_ever_participated:
                continue

            saturation = min(1.0, elapsed / SATURATION_SECONDS)
            strength = MAX_STRENGTH * saturation

            self._last_emitted_t[state.participant_id] = now_t
            await self.emit(
                ctx,
                participant_id=state.participant_id,
                signal="silent_presence",
                direction="against_candidate",
                strength=strength,
                reasoning=(
                    f"Present for {elapsed:.0f}s with no speaking, no webcam, no "
                    "screenshare, and no transcript content - consistent with a "
                    "silent observer rather than the person being interviewed."
                ),
                t=now_t,
            )
