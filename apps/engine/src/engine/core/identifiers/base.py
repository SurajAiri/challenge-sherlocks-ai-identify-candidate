"""
Identifier base class - the pluggable unit of "weak signal" reasoning,
and the *emitting* subset of Processor (see core/processor.py for the
full writeup of the shared kind/run_mode/listens_to/depends_on axes -
they're defined once, there, and Identifier inherits them unchanged).

An Identifier adds exactly one thing on top of a plain Processor:
`emit()`, i.e. the ability to publish Evidence for the Belief Engine.
A plain Processor cannot do this - if something needs to move the
candidate-probability needle, it must be an Identifier; if it's purely
reusable computation other things depend on (decode, embed, ...), it
should be a Processor instead. This split is what lets an expensive
extraction step be shared by several Identifiers without any of them
having to also be "the thing that owns whether this counts as
evidence."

`IdentifierKind`/`IdentifierRunMode` are re-exported aliases of
`ProcessorKind`/`ProcessorRunMode` (not separate enums) precisely so
this axis stays single-vocabulary across both layers, per the earlier
discussion - existing identifiers that import these names keep working
unchanged, they're just importing the same enum under its established
name here.

Both hooks receive an `IdentifierContext` giving them a *read-only*
view of participant state, a *read-only* view of the Feature Cache
(for reading any declared `depends_on` Processor's output), and an
`emit` callback to publish Evidence. Identifiers never write to the
state store or the Feature Cache directly and never talk to each
other directly - all coordination happens through Evidence (for
identification results) or the Feature Cache (for shared computation),
which keeps each identifier independently pluggable/testable/removable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from engine.core.processor import Processor, ProcessorContext, ProcessorKind, ProcessorRunMode
from engine.core.schemas import Evidence, EvidenceDirection, SimEvent

# Single-vocabulary axis shared with Processor - see module docstring.
IdentifierKind = ProcessorKind
IdentifierRunMode = ProcessorRunMode

EmitFn = Callable[[Evidence], Awaitable[None]]


@dataclass
class IdentifierContext(ProcessorContext):
    emit: EmitFn


class Identifier(Processor):
    """Subclass this, set the class attributes, override the hook(s)
    you need. Nothing is abstract-required beyond `id` - an identifier
    that only implements `on_join` (and not `on_event`) is valid, and
    vice versa."""

    # Relative weight this identifier's evidence carries once combined
    # by the Belief Engine. Tune per-identifier as false-positive rate
    # becomes known from evaluation - this is the "weighted" in
    # "Pluggable Weighted Continuous Identifiers". Meaningless (and
    # unused) on a plain Processor, which is why it lives here and not
    # on the shared base.
    weight: float = 1.0

    async def on_join(self, participant_id: str, ctx: IdentifierContext) -> None:  # type: ignore[override]
        """Called once, immediately after a participant entity is
        created (before the continuous loop is even running for that
        participant), if run_mode is ONE_TIME or BOTH."""
        return None

    async def on_event(self, event: SimEvent, ctx: IdentifierContext) -> None:  # type: ignore[override]
        """Called for every event whose type is in `listens_to`, if
        run_mode is CONTINUOUS or BOTH."""
        return None

    # -- convenience for subclasses --------------------------------------

    async def emit(
        self,
        ctx: IdentifierContext,
        *,
        participant_id: Optional[str],
        signal: str,
        direction: EvidenceDirection,
        strength: float,
        reasoning: str,
        t: float,
    ) -> None:
        strength = max(0.0, min(1.0, strength))
        await ctx.emit(
            Evidence(
                identifier_id=self.id,
                participant_id=participant_id,
                signal=signal,
                direction=direction,
                strength=strength,
                reasoning=reasoning,
                t=t,
            )
        )
