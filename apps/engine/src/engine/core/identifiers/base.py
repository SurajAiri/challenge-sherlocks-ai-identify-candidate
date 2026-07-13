"""
Identifier base class - the pluggable unit of "weak signal" reasoning.

Two independent axes, per the architecture notes:
  - IdentifierKind: INSTANT (reacts to a single event in isolation) vs
    TEMPORAL (reasons about accumulated state / a window over time).
    This is about *what the identifier looks at*.
  - IdentifierRunMode: ONE_TIME (runs exactly once, right when a
    participant is created) vs CONTINUOUS (runs again on every
    matching event for the lifetime of the session). This is about
    *when the identifier runs*.

A concrete identifier can implement either or both hooks:
  - `on_join`   -> invoked once by the "Initial One Time Run" step.
  - `on_event`  -> invoked by the continuous Event Bus for every event
                   whose type is in `listens_to`.

Both hooks receive an `IdentifierContext` giving them a *read-only*
view of participant state plus an `emit` callback to publish Evidence.
Identifiers never write to the state store directly and never talk to
each other directly - all coordination happens through evidence, which
keeps each identifier independently pluggable/testable/removable.
"""
from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Optional

from engine.core.schemas import Evidence, EvidenceDirection, SimEvent
from engine.core.state_store import ParticipantStateReadOnlyView


class IdentifierKind(str, Enum):
    INSTANT = "instant"
    TEMPORAL = "temporal"


class IdentifierRunMode(str, Enum):
    ONE_TIME = "one_time"
    CONTINUOUS = "continuous"
    BOTH = "both"


EmitFn = Callable[[Evidence], Awaitable[None]]


@dataclass
class IdentifierContext:
    state: ParticipantStateReadOnlyView
    emit: EmitFn


class Identifier(ABC):
    """Subclass this, set the class attributes, override the hook(s)
    you need. Nothing is abstract-required beyond `id` - an identifier
    that only implements `on_join` (and not `on_event`) is valid, and
    vice versa."""

    id: str = "unnamed_identifier"
    # Relative weight this identifier's evidence carries once combined
    # by the Belief Engine. Tune per-identifier as false-positive rate
    # becomes known from evaluation - this is the "weighted" in
    # "Pluggable Weighted Continuous Identifiers".
    weight: float = 1.0
    kind: IdentifierKind = IdentifierKind.INSTANT
    run_mode: IdentifierRunMode = IdentifierRunMode.CONTINUOUS
    # SimEventType values (as strings) this identifier wants delivered
    # to `on_event`. Use {"*"} to receive every event type.
    listens_to: frozenset[str] = frozenset()

    async def on_join(self, participant_id: str, ctx: IdentifierContext) -> None:
        """Called once, immediately after a participant entity is
        created (before the continuous loop is even running for that
        participant), if run_mode is ONE_TIME or BOTH."""
        return None

    async def on_event(self, event: SimEvent, ctx: IdentifierContext) -> None:
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
