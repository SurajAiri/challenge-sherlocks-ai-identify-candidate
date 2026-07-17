"""
Processor base class - the pluggable unit of reusable, dependency-
ordered work. This is the generalization of what used to be just
`Identifier`: an Identifier (see core/identifiers/base.py) *is* a
Processor, specifically the subset that's also allowed to emit
Evidence. A plain Processor is not - it exists purely to do reusable
computation (decode a video frame, embed a face, extract a speaker
embedding, ...) that one or more Identifiers - or other Processors -
depend on, so that expensive work happens once and gets shared rather
than being duplicated inside every Identifier that needs it.

Three axes, all shared with Identifier (which inherits them):
  - ProcessorKind: INSTANT (reacts to a single event in isolation) vs
    TEMPORAL (reasons about accumulated state / a window over time).
    What the processor looks at.
  - ProcessorRunMode: ONE_TIME (runs exactly once, at participant
    creation) vs CONTINUOUS (runs again on every matching event) vs
    BOTH. When the processor runs.
  - depends_on: the new axis. A frozenset of other processor ids this
    one needs to have already run (this tick) before it's meaningful
    to run this one. ProcessorRegistry uses this to compute both a
    dependency-respecting run order (see core/registry.py) and which
    processors are `enabled` at all (a processor nothing downstream
    ever consumes is dead weight and should never actually run).

Push stays the delivery mechanism end to end - a Processor still
subscribes to `listens_to` events exactly like an Identifier does
today, it does NOT get lazily pulled by its dependents. `depends_on`
only affects *ordering* (within the same event's dispatch) and
*gating* (enabled/disabled, and "is my dependency's output actually
there yet"), never *triggering*.

A Processor's `on_join`/`on_event` may return a value. If it returns
something other than None, the caller (SessionEngine) records that
value into the Feature Cache under this processor's id - that's the
processor's "reusable output" becoming available to whatever declared
a dependency on it. Returning None means "nothing new to publish this
tick," which is different from *failing* - it doesn't evict what's
already cached.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from engine.core.feature_cache import FeatureCacheReadView
from engine.core.scheduler import SchedulingTier
from engine.core.schemas import SimEvent
from engine.core.state_store import ParticipantStateReadOnlyView


class ProcessorKind(str, Enum):
    INSTANT = "instant"
    TEMPORAL = "temporal"


class ProcessorRunMode(str, Enum):
    ONE_TIME = "one_time"
    CONTINUOUS = "continuous"
    BOTH = "both"


# Feature Cache retention, in units of "this processor's own run
# count" (not wall-clock time, not event count) - a processor that
# runs rarely doesn't lose its cached output just because a lot of
# *unrelated* events flowed through in the meantime. Default is enough
# for a couple of downstream temporal reads; MAX exists so a
# misconfigured processor can't quietly retain unbounded history over
# a 30+ minute call.
DEFAULT_CACHE_TTL_ITERATIONS = 5
MAX_CACHE_TTL_ITERATIONS = 20


def clamp_cache_ttl_iterations(n: int) -> int:
    return max(1, min(int(n), MAX_CACHE_TTL_ITERATIONS))


@dataclass
class ProcessorContext:
    state: ParticipantStateReadOnlyView
    # Read-only view onto every processor's Feature Cache output,
    # including this processor's own dependencies (and, in principle,
    # anything else - nothing stops a processor from peeking at a
    # non-declared key, same "convention not enforcement" trade-off as
    # ParticipantStateReadOnlyView). Use `cache.latest(dep_id)` /
    # `cache.window(dep_id)`.
    cache: FeatureCacheReadView


class Processor(ABC):
    """Subclass this, set the class attributes, override the hook(s)
    you need. See Identifier (core/identifiers/base.py) if what you're
    building should also emit Evidence - most identification logic
    wants that subclass, not this one directly."""

    id: str = "unnamed_processor"
    kind: ProcessorKind = ProcessorKind.INSTANT
    run_mode: ProcessorRunMode = ProcessorRunMode.CONTINUOUS
    # SimEventType values (as strings) this processor wants delivered
    # to `on_event`. Use {"*"} to receive every event type.
    listens_to: frozenset[str] = frozenset()

    # Opt-in scheduling throttle, identical mechanism to Identifier's
    # (see core/scheduler.py) - empty dict means never throttled.
    min_interval_by_tier: dict[SchedulingTier, float] = {}

    # Other processor ids (Processor or Identifier - Identifiers can
    # depend on Processors, and Processors can depend on other
    # Processors) that must run before this one, this tick, if this
    # one is to run meaningfully at all. ProcessorRegistry validates
    # every id here actually exists in the registry and that the full
    # graph is acyclic at build time - see core/registry.py.
    depends_on: frozenset[str] = frozenset()

    # How many of this processor's own past outputs to retain per
    # (participant_id or None) key. Clamped to MAX_CACHE_TTL_ITERATIONS
    # by the Feature Cache regardless of what's declared here.
    cache_ttl_iterations: int = DEFAULT_CACHE_TTL_ITERATIONS

    # Computed once by ProcessorRegistry at build time - never set by
    # hand on a subclass. True iff at least one Identifier is
    # reachable from this processor via the depends_on graph, i.e.
    # something eventually consumes its output. SessionEngine still
    # calls a disabled processor's hooks (so behavior here is uniform
    # regardless of registry wiring details) but the processor itself
    # is expected to have nothing meaningful to do - concretely,
    # SessionEngine's dispatch checks this flag first and skips
    # invoking the hook at all when it's False, so a disabled
    # processor never runs, never pays scheduler/dependency-check
    # cost, and never touches the cache.
    enabled: bool = True

    async def on_join(
        self, participant_id: str, ctx: ProcessorContext
    ) -> Optional[Any]:
        """Called once, immediately after a participant entity is
        created, if run_mode is ONE_TIME or BOTH. Return a value to
        publish it into the Feature Cache under this processor's id;
        return None for "nothing to cache yet."""
        return None

    async def on_event(self, event: SimEvent, ctx: ProcessorContext) -> Optional[Any]:
        """Called for every event whose type is in `listens_to`, if
        run_mode is CONTINUOUS or BOTH. Return a value to publish it
        into the Feature Cache under this processor's id; return None
        for "nothing new this tick" (does not evict prior output)."""
        return None
