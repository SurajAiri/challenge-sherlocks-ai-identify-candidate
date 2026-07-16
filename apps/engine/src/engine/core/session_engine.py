"""
SessionEngine - one instance per live WebSocket connection (i.e. per
interview session). This is the box labeled "Engine (continuous loop)"
in the architecture diagram, wired up end to end:

    inbound SimFrame
        -> ParticipantStateRepository.apply_event/apply_stream_frame
        -> (if new participant) Initial One Time Run
        -> Event Bus -> Pluggable Continuous Processors (Extractors +
           Identifiers, dependency-ordered - see core/registry.py)
        -> Evidence Bus -> Evidence Normalizer -> Belief Engine
        -> Output Formatter -> outbound EngineMessage

"Continuous loop" is realized reactively (recompute-and-emit on every
frame that could plausibly change the answer) rather than as a fixed
polling interval - real interview events are sparse enough that
polling would either lag behind speech or waste cycles, and reacting
directly to each event gives lower latency for free. A periodic
heartbeat is layered on top (see `heartbeat()`) purely so the dashboard
still gets a fresh snapshot even during a long silent stretch, e.g. a
take-home coding pause with no new events.

Processors (see core/processor.py) sit below Identifiers: they're
pluggable, dependency-ordered, pushed off the same event bus, but they
don't emit Evidence - they publish reusable output into the Feature
Cache (core/feature_cache.py) for whatever declared a dependency on
them. `_run_hook` is the one place that understands how a Processor
call is gated - `enabled` (computed once by the registry - nothing
downstream ever consumes this processor, so don't bother running it),
the Scheduler (opt-in per-tier throttling, unchanged from before), and
`depends_on` (every dependency must already have produced *something*
in the Feature Cache, or this call is skipped and logged - see
FeatureCache.satisfied for exactly what "something" means).
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from engine.core.belief_engine import BeliefEngine
from engine.core.event_bus import EventBus
from engine.core.evidence_normalizer import normalize
from engine.core.feature_cache import FeatureCache
from engine.core.identifiers.base import Identifier, IdentifierContext
from engine.core.processor import Processor, ProcessorContext, clamp_cache_ttl_iterations
from engine.core.registry import ProcessorRegistry, default_registry
from engine.core.output_formatter import format_message
from engine.core.scheduler import Scheduler
from engine.core.schemas import (
    ContextFrame,
    ErrorFrame,
    Evidence,
    SimEventFrame,
    SimFrame,
    StreamFrameEnvelope,
)
from engine.core.state_store import ParticipantStateRepository

logger = logging.getLogger("engine.session")

SendFn = Callable[[dict], Awaitable[None]]

# Frame kinds that can plausibly move the needle and are therefore
# worth recomputing + re-emitting a snapshot for. `context` alone
# rarely does (no participant evidence yet), `error` never does.
SNAPSHOT_TRIGGERING_KINDS = {"event", "stream"}


class SessionEngine:
    def __init__(self, send: SendFn, registry: ProcessorRegistry | None = None) -> None:
        self.send = send
        self.registry = registry or default_registry()

        self.store = ParticipantStateRepository()
        self.feature_cache = FeatureCache()
        self.raw_bus = EventBus(name="raw_events")
        self.evidence_bus = EventBus(name="evidence")
        self.belief_engine = BeliefEngine()
        # Tier follows DetectionState (see belief_engine.recompute_probabilities
        # -> DetectionStateTracker.update, called after every evidence update,
        # i.e. before the *next* event's processors run). Only processors
        # that opt in via `min_interval_by_tier` are ever throttled.
        self.scheduler = Scheduler()

        # Every identifier's evidence flows through the same
        # normalize -> belief-engine pipeline, regardless of which
        # identifier or which run mode produced it.
        self.evidence_bus.subscribe("*", self._consume_evidence)
        self._wire_continuous_processors()

    def _wire_continuous_processors(self) -> None:
        """Subscribes every CONTINUOUS/BOTH processor (Extractors and
        Identifiers alike) to its declared event types on the raw
        event bus - the dashed arrows from "Event Bus" to each
        processor box in the diagram. One-time processors are invoked
        separately, directly, from `_run_initial_processors` (they
        don't listen on the bus at all - they run exactly once, off
        the join trigger).

        Filtering is delegated to `registry.continuous_for_event_type`
        rather than iterating `processor.listens_to` directly - both
        `run_mode` (a ONE_TIME processor that also happens to declare
        `listens_to` must never get `on_event` called repeatedly) and
        dependency order (a processor must be offered to the bus, for
        a given event type, no earlier than anything it depends on
        that's also subscribed to that type) are the registry's job,
        not this method's.
        """
        all_event_types = {
            event_type for processor in self.registry.processors for event_type in processor.listens_to
        }
        for event_type in all_event_types:
            for processor in self.registry.continuous_for_event_type(event_type):

                async def _invoke(event, _processor=processor) -> None:
                    participant_id = getattr(event, "participant_id", None)
                    ctx = self._make_ctx_for(_processor)
                    await self._run_hook(
                        _processor,
                        participant_id=participant_id,
                        t=self.store.current_t,
                        invoke=lambda: _processor.on_event(event, ctx),
                    )

                self.raw_bus.subscribe(event_type, _invoke)

    def _make_ctx_for(self, processor: Processor) -> ProcessorContext:
        # Fresh read-only views per call so processors always see
        # latest state/cache, not a stale snapshot captured at wiring
        # time.
        if isinstance(processor, Identifier):
            return IdentifierContext(
                state=self.store.read_only_view(),
                cache=self.feature_cache.read_only_view(),
                emit=self._publish_evidence,
            )
        return ProcessorContext(state=self.store.read_only_view(), cache=self.feature_cache.read_only_view())

    async def _run_hook(
        self,
        processor: Processor,
        *,
        participant_id: Optional[str],
        t: float,
        invoke: Callable[[], Awaitable[Optional[object]]],
    ) -> None:
        """The one place that understands how any Processor hook call
        (on_join or on_event) is gated, shared by both the continuous
        bus dispatch and the one-time join run:

          1. `enabled` - computed once by the registry (nothing
             downstream ever consumes this processor's output) - skip
             entirely, no scheduler/dependency cost paid.
          2. Scheduler - unchanged opt-in per-tier throttling.
          3. `depends_on` - every declared dependency must already
             have produced *something* in the Feature Cache (see
             FeatureCache.satisfied) or this call is skipped and
             logged as a warning. No cold-start special case: "never
             ran yet" and "ran once, nothing since" are both just
             "not satisfied" under this cache's run-count-based
             retention (see feature_cache.py docstring).

        On an actual call, a non-None return value is recorded into
        the Feature Cache under this processor's id.
        """
        if not processor.enabled:
            return

        if participant_id is not None and not self.scheduler.may_run(
            processor.id, participant_id, t, processor.min_interval_by_tier
        ):
            return

        for dep_id in processor.depends_on:
            if not self.feature_cache.satisfied(dep_id, participant_id):
                logger.warning(
                    "processor %r skipped: dependency %r not satisfied "
                    "(participant_id=%r, t=%s) - expected if it hasn't run "
                    "yet this session, unexpected otherwise",
                    processor.id,
                    dep_id,
                    participant_id,
                    t,
                )
                return

        try:
            result = await invoke()
        except Exception:
            logger.exception("processor %r failed", processor.id)
            return

        if result is not None:
            self.feature_cache.record(
                processor.id,
                participant_id,
                result,
                t,
                maxlen=clamp_cache_ttl_iterations(processor.cache_ttl_iterations),
            )

        if participant_id is not None:
            self.scheduler.record_run(processor.id, participant_id, t)

    async def _publish_evidence(self, evidence: Evidence) -> None:
        await self.evidence_bus.publish("*", evidence)

    async def _consume_evidence(self, evidence: Evidence) -> None:
        processor = self.registry.get(evidence.identifier_id)
        identifier_weight = processor.weight if isinstance(processor, Identifier) else 1.0
        decay_half_life = processor.decay_half_life if isinstance(processor, Identifier) else None
        normalized = normalize(evidence, identifier_weight, decay_half_life)
        self.belief_engine.apply(self.store, normalized)
        # Belief just moved -> re-derive detection state -> re-derive
        # scheduling tier, so the *next* incoming event is throttled
        # (or not) according to where the session's confidence actually
        # stands right now, not last message's tier.
        self.scheduler.set_tier_from_state(self.belief_engine.current_detection_state)

    # -- frame ingestion --------------------------------------------------

    async def handle_frame(self, frame: SimFrame) -> None:
        try:
            await self._dispatch(frame)
        except Exception:
            logger.exception("error handling frame kind=%s", frame.kind)
            return

        if frame.kind in SNAPSHOT_TRIGGERING_KINDS:
            await self._emit_snapshot()

    async def _dispatch(self, frame: SimFrame) -> None:
        if isinstance(frame, ContextFrame):
            self.store.set_context(frame.payload)
        elif isinstance(frame, SimEventFrame):
            event = frame.payload
            state, is_new = self.store.apply_event(event)
            if is_new and state is not None:
                await self._run_initial_processors(state.participant_id)
            await self.raw_bus.publish(event.type.value, event)
        elif isinstance(frame, StreamFrameEnvelope):
            self.store.apply_stream_frame(frame.payload)
            await self.raw_bus.publish("stream", frame.payload)
        elif isinstance(frame, ErrorFrame):
            logger.warning("upstream reported error frame: %r", frame.payload)

    async def _run_initial_processors(self, participant_id: str) -> None:
        """The 'Initial One Time Run' box: fires once per participant,
        before the continuous loop has produced anything for them.
        Steps per the diagram: processors get read-only State Store
        (and Feature Cache) access, Identifier results flow through
        the same normalizer -> belief pipeline as everything else -
        only the *trigger* (join, not an event type) and the
        *cardinality* (once) differ from continuous processors.
        `registry.one_time()` already returns them in dependency
        order, same reasoning as the continuous bus."""
        for processor in self.registry.one_time():
            ctx = self._make_ctx_for(processor)
            await self._run_hook(
                processor,
                participant_id=participant_id,
                t=self.store.current_t,
                invoke=lambda p=processor, c=ctx: p.on_join(participant_id, c),
            )

    async def _emit_snapshot(self) -> None:
        message = format_message(self.store, self.belief_engine.current_detection_state)
        await self.send(message.model_dump(mode="json"))

    async def heartbeat(self) -> None:
        """Re-emit the current snapshot with no new evidence. This is
        also the only place decay becomes visible during a quiet
        stretch: recompute_probabilities re-derives every participant's
        probabilities from their identifier_contributions' *currently*
        decayed values (see belief_engine.py) using the elapsed time
        since each contribution's last touch - without this call,
        decay would only ever become visible on the next actual event,
        which defeats the point during a long silence."""
        self.belief_engine.recompute_probabilities(self.store)
        await self._emit_snapshot()
