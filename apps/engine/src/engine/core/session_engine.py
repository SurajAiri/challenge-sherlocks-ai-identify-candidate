"""
SessionEngine - one instance per live WebSocket connection (i.e. per
interview session). This is the box labeled "Engine (continuous loop)"
in the architecture diagram, wired up end to end:

    inbound SimFrame
        -> ParticipantStateRepository.apply_event/apply_stream_frame
        -> (if new participant) Initial One Time Run
        -> Event Bus -> Pluggable Continuous Identifiers
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
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from engine.core.belief_engine import BeliefEngine
from engine.core.event_bus import EventBus
from engine.core.evidence_normalizer import normalize
from engine.core.identifiers.base import IdentifierContext
from engine.core.identifiers.registry import IdentifierRegistry, default_registry
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
    def __init__(self, send: SendFn, registry: IdentifierRegistry | None = None) -> None:
        self.send = send
        self.registry = registry or default_registry()

        self.store = ParticipantStateRepository()
        self.raw_bus = EventBus(name="raw_events")
        self.evidence_bus = EventBus(name="evidence")
        self.belief_engine = BeliefEngine()
        # Tier follows DetectionState (see belief_engine.recompute_probabilities
        # -> DetectionStateTracker.update, called after every evidence update,
        # i.e. before the *next* event's identifiers run). Only identifiers
        # that opt in via `min_interval_by_tier` are ever throttled.
        self.scheduler = Scheduler()

        # Every identifier's evidence flows through the same
        # normalize -> belief-engine pipeline, regardless of which
        # identifier or which run mode produced it.
        self.evidence_bus.subscribe("*", self._consume_evidence)
        self._wire_continuous_identifiers()

    def _wire_continuous_identifiers(self) -> None:
        """Subscribes every CONTINUOUS/BOTH identifier to its declared
        event types on the raw event bus - the dashed arrows from
        "Event Bus" to each identifier box in the diagram. One-time
        identifiers are invoked separately, directly, from
        `_run_initial_identifiers` (they don't listen on the bus at
        all - they run exactly once, off the join trigger).

        Filtering is delegated to `registry.continuous_for_event_type`
        rather than iterating `identifier.listens_to` directly - an
        identifier's `run_mode` must be respected here: a ONE_TIME
        identifier that also happens to declare `listens_to` (e.g. for
        future use, or by copy-paste from a continuous one) must never
        get `on_event` called repeatedly, since that would silently
        violate its "runs exactly once" contract.
        """
        all_event_types = {
            event_type for identifier in self.registry.identifiers for event_type in identifier.listens_to
        }
        for event_type in all_event_types:
            for identifier in self.registry.continuous_for_event_type(event_type):

                async def _invoke(event, _identifier=identifier) -> None:
                    # Fresh read-only view per call so identifiers
                    # always see latest state, not a stale snapshot
                    # captured at wiring time.
                    participant_id = getattr(event, "participant_id", None)
                    if participant_id is not None and not self.scheduler.may_run(
                        _identifier.id,
                        participant_id,
                        self.store.current_t,
                        _identifier.min_interval_by_tier,
                    ):
                        return
                    await _identifier.on_event(event, self._make_identifier_ctx())
                    if participant_id is not None:
                        self.scheduler.record_run(_identifier.id, participant_id, self.store.current_t)

                self.raw_bus.subscribe(event_type, _invoke)

    def _make_identifier_ctx(self) -> IdentifierContext:
        return IdentifierContext(state=self.store.read_only_view(), emit=self._publish_evidence)

    async def _publish_evidence(self, evidence: Evidence) -> None:
        await self.evidence_bus.publish("*", evidence)

    async def _consume_evidence(self, evidence: Evidence) -> None:
        identifier_weight = next(
            (i.weight for i in self.registry.identifiers if i.id == evidence.identifier_id), 1.0
        )
        normalized = normalize(evidence, identifier_weight)
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
                await self._run_initial_identifiers(state.participant_id)
            await self.raw_bus.publish(event.type.value, event)
        elif isinstance(frame, StreamFrameEnvelope):
            self.store.apply_stream_frame(frame.payload)
            await self.raw_bus.publish("stream", frame.payload)
        elif isinstance(frame, ErrorFrame):
            logger.warning("upstream reported error frame: %r", frame.payload)

    async def _run_initial_identifiers(self, participant_id: str) -> None:
        """The 'Initial One Time Run' box: fires once per participant,
        before the continuous loop has produced anything for them.
        Steps per the diagram: identifiers get read-only State Store
        access, results flow through the same normalizer -> belief
        pipeline as everything else - only the *trigger* (join, not an
        event type) and the *cardinality* (once) differ from
        continuous identifiers."""
        ctx = self._make_identifier_ctx()
        for identifier in self.registry.one_time():
            try:
                await identifier.on_join(participant_id, ctx)
            except Exception:
                logger.exception("identifier %r failed in on_join", identifier.id)

    async def _emit_snapshot(self) -> None:
        message = format_message(self.store, self.belief_engine.current_detection_state)
        await self.send(message.model_dump(mode="json"))

    async def heartbeat(self) -> None:
        """Re-emit the current snapshot with no new evidence - keeps
        the dashboard's confidence display fresh (same numbers, bumped
        timestamp) even through a long stretch with no events."""
        await self._emit_snapshot()

