"""
SpeakingShareIdentifier.

Temporal, continuous: on every speaking_end, recompute this
participant's share of total speaking time across everyone in the
call so far. A candidate being interviewed tends to hold a large,
sustained share of speaking time (answering questions at length),
while interviewers speak in shorter bursts (asking questions,
transitioning topics) and observers often don't speak at all.

Deliberately does NOT fire until a minimum amount of total speaking
time has accumulated (`MIN_TOTAL_SECONDS_BEFORE_SIGNAL`) - speaking
share is meaningless noise in the first few seconds of a call (whoever
happens to say "hi" first would look like 100% of the signal), and an
identifier that emits confident-looking evidence off two seconds of
audio is worse than one that stays quiet until it has something real
to say.
"""
from __future__ import annotations

from engine.core.identifiers.base import Identifier, IdentifierContext, IdentifierKind, IdentifierRunMode
from engine.core.schemas import SimEvent, SimEventType

MIN_TOTAL_SECONDS_BEFORE_SIGNAL = 8.0

# A share above this is treated as "dominant" and produces for_candidate
# evidence; a share far below the fair/even split for the number of
# participants present is treated as against_candidate (someone barely
# speaking is unlikely to be the person being interviewed - though this
# is deliberately gentle, since a nervous or terse candidate is
# plausible and this should never be the only signal in play).
DOMINANT_SHARE_THRESHOLD = 0.45


class SpeakingShareIdentifier(Identifier):
    id = "speaking_share"
    weight = 0.7
    kind = IdentifierKind.TEMPORAL
    run_mode = IdentifierRunMode.CONTINUOUS
    listens_to = frozenset({SimEventType.SPEAKING_END.value})

    async def on_event(self, event: SimEvent, ctx: IdentifierContext) -> None:
        if event.participant_id is None:
            return

        present = ctx.state.present() or ctx.state.all()
        total = sum(p.total_speaking_seconds for p in present)
        if total < MIN_TOTAL_SECONDS_BEFORE_SIGNAL or len(present) < 2:
            return

        speaker = ctx.state.get(event.participant_id)
        if speaker is None:
            return

        share = speaker.total_speaking_seconds / total
        fair_share = 1.0 / len(present)

        if share >= DOMINANT_SHARE_THRESHOLD:
            # Scale strength by how far above the dominant threshold we
            # are, saturating rather than growing unbounded.
            strength = min(1.0, (share - DOMINANT_SHARE_THRESHOLD) / (1.0 - DOMINANT_SHARE_THRESHOLD) + 0.3)
            await self.emit(
                ctx,
                participant_id=event.participant_id,
                signal="dominant_speaking_share",
                direction="for_candidate",
                strength=strength,
                reasoning=(
                    f"Holds {share:.0%} of total speaking time across {len(present)} "
                    f"participants ({speaker.total_speaking_seconds:.0f}s), well above an even split."
                ),
                t=event.t,
            )
        elif share < fair_share * 0.35:
            await self.emit(
                ctx,
                participant_id=event.participant_id,
                signal="low_speaking_share",
                direction="against_candidate",
                strength=0.3,
                reasoning=(
                    f"Only {share:.0%} of total speaking time so far - well below an even "
                    f"split for {len(present)} participants, more consistent with an "
                    f"interviewer or silent observer than the person being interviewed."
                ),
                t=event.t,
            )
