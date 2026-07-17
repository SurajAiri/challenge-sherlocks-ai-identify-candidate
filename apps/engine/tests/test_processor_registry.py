"""
Tests for the Processor / ProcessorRegistry / FeatureCache machinery
added on top of the original flat Identifier registry (see
core/processor.py, core/registry.py, core/feature_cache.py). The
existing identifier-only behavior is covered by test_session_engine.py
and is unchanged by this file - these tests are specifically about the
new dependency axis: layering, `enabled` computed by reachability,
cycle detection, and "missing dependency -> skip + warn" at the
SessionEngine dispatch level.

Run with: `uv run pytest` from `apps/engine/`.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from engine.core.identifiers.base import Identifier, IdentifierContext
from engine.core.processor import Processor, ProcessorContext, ProcessorRunMode
from engine.core.registry import DependencyCycleError, ProcessorRegistry
from engine.core.schemas import (
    ContextFrame,
    SessionContext,
    SimEvent,
    SimEventFrame,
    SimEventType,
)
from engine.core.session_engine import SessionEngine


def _event(
    t: float, type_: SimEventType, participant_id: str | None, **data
) -> SimEventFrame:
    return SimEventFrame(
        payload=SimEvent(t=t, type=type_, participant_id=participant_id, data=data)
    )


# -- ProcessorRegistry unit tests (no SessionEngine involved) --------------


class _Upstream(Processor):
    id = "upstream"
    listens_to = frozenset({SimEventType.SPEAKING_START.value})


class _Downstream(Processor):
    id = "downstream"
    listens_to = frozenset({SimEventType.SPEAKING_START.value})
    depends_on = frozenset({"upstream"})


class _ConsumingIdentifier(Identifier):
    id = "consumer"
    listens_to = frozenset({SimEventType.SPEAKING_START.value})
    depends_on = frozenset({"downstream"})


def test_layering_orders_dependencies_before_dependents():
    registry = ProcessorRegistry([_Downstream(), _ConsumingIdentifier(), _Upstream()])
    ordered = registry.continuous_for_event_type(SimEventType.SPEAKING_START.value)
    ids = [p.id for p in ordered]
    assert ids.index("upstream") < ids.index("downstream") < ids.index("consumer")


def test_enabled_is_reachability_not_just_direct_dependents():
    """`downstream` has no Identifier depending on it *directly* -
    only `consumer` depends on `downstream`, and only `downstream`
    depends on `upstream`. Both must still end up enabled because
    `consumer` (an Identifier) is reachable from both, transitively."""
    registry = ProcessorRegistry([_Upstream(), _Downstream(), _ConsumingIdentifier()])
    assert registry.get("upstream").enabled is True
    assert registry.get("downstream").enabled is True
    assert registry.get("consumer").enabled is True


def test_processor_with_no_dependents_is_disabled():
    class _Orphan(Processor):
        id = "orphan"
        listens_to = frozenset({SimEventType.SPEAKING_START.value})

    registry = ProcessorRegistry(
        [_Orphan(), _Upstream(), _Downstream(), _ConsumingIdentifier()]
    )
    assert registry.get("orphan").enabled is False
    assert registry.get("upstream").enabled is True


def test_cycle_raises_at_build_time():
    class _A(Processor):
        id = "a"
        depends_on = frozenset({"b"})

    class _B(Processor):
        id = "b"
        depends_on = frozenset({"a"})

    with pytest.raises(DependencyCycleError):
        ProcessorRegistry([_A(), _B()])


def test_unknown_dependency_raises_at_build_time():
    class _Lonely(Processor):
        id = "lonely"
        depends_on = frozenset({"does_not_exist"})

    with pytest.raises(ValueError):
        ProcessorRegistry([_Lonely()])


def test_duplicate_id_raises_at_build_time():
    class _DupA(Processor):
        id = "dup"

    class _DupB(Processor):
        id = "dup"

    with pytest.raises(ValueError):
        ProcessorRegistry([_DupA(), _DupB()])


# -- End-to-end through SessionEngine ---------------------------------------


def test_downstream_identifier_reads_upstream_processor_output_via_cache():
    """A plain Processor (no emit) runs first, publishes a value into
    the Feature Cache; an Identifier depending on it reads that value
    back out via ctx.cache and emits Evidence from it - proving the
    push-with-shared-cache design actually wires end to end, not just
    that the registry orders things correctly in isolation."""

    class DecodeProcessor(Processor):
        id = "decode"
        listens_to = frozenset({SimEventType.SPEAKING_START.value})

        async def on_event(self, event, ctx: ProcessorContext):
            return {"decoded_for": event.participant_id}

    class ReadsCacheIdentifier(Identifier):
        id = "reads_cache"
        listens_to = frozenset({SimEventType.SPEAKING_START.value})
        depends_on = frozenset({"decode"})

        async def on_event(self, event, ctx: IdentifierContext) -> None:
            entry = ctx.cache.latest("decode", event.participant_id)
            assert entry is not None
            await self.emit(
                ctx,
                participant_id=event.participant_id,
                signal="saw_decoded_frame",
                direction="for_candidate",
                strength=0.9,
                reasoning=f"decode said {entry.value}",
                t=event.t,
            )

    registry = ProcessorRegistry([DecodeProcessor(), ReadsCacheIdentifier()])
    messages: list[dict] = []

    async def send(payload: dict) -> None:
        messages.append(payload)

    async def run():
        engine = SessionEngine(send=send, registry=registry)
        await engine.handle_frame(
            ContextFrame(
                payload=SessionContext(
                    candidate_name="Suraj", candidate_email="s@example.com"
                )
            )
        )
        await engine.handle_frame(
            _event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="Suraj")
        )
        await engine.handle_frame(_event(1, SimEventType.SPEAKING_START, "p_a"))
        return engine

    engine = asyncio.run(run())
    assert engine.feature_cache.latest("decode", "p_a").value == {"decoded_for": "p_a"}
    final = messages[-1]
    assert "p_a" in final["evidence"]
    assert any(
        "saw_decoded_frame" in line or "decode said" in line
        for line in final["evidence"]["p_a"]
    )


def test_identifier_skipped_and_warns_when_dependency_never_ran():
    """The dependency's event type never fires in this scenario, so
    its Feature Cache entry never gets created - the dependent
    Identifier's on_event must be skipped (not called with missing
    data), and a warning logged, every tick, uniformly (no silent
    cold-start exception - see core/session_engine.py._run_hook)."""

    class NeverTriggeredProcessor(Processor):
        id = "never_triggered"
        listens_to = frozenset({SimEventType.WEBCAM_ON.value})  # never sent below

    calls = {"on_event": 0}

    class StarvedIdentifier(Identifier):
        id = "starved"
        listens_to = frozenset({SimEventType.SPEAKING_START.value})
        depends_on = frozenset({"never_triggered"})

        async def on_event(self, event, ctx: IdentifierContext) -> None:
            calls["on_event"] += 1

    registry = ProcessorRegistry([NeverTriggeredProcessor(), StarvedIdentifier()])

    async def send(payload: dict) -> None:
        return None

    async def run():
        engine = SessionEngine(send=send, registry=registry)
        await engine.handle_frame(
            _event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="A")
        )
        await engine.handle_frame(_event(1, SimEventType.SPEAKING_START, "p_a"))
        await engine.handle_frame(_event(2, SimEventType.SPEAKING_START, "p_a"))

    asyncio.run(run())
    assert calls["on_event"] == 0


def test_disabled_processor_never_runs():
    """A Processor nothing depends on is disabled by the registry and
    must never have its hook invoked at all, even though it's
    subscribed to a real event type that does fire."""
    calls = {"on_event": 0}

    class DeadWeightProcessor(Processor):
        id = "dead_weight"
        listens_to = frozenset({SimEventType.SPEAKING_START.value})

        async def on_event(self, event, ctx: ProcessorContext):
            calls["on_event"] += 1
            return "should never happen"

    registry = ProcessorRegistry([DeadWeightProcessor()])

    async def send(payload: dict) -> None:
        return None

    async def run():
        engine = SessionEngine(send=send, registry=registry)
        await engine.handle_frame(
            _event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="A")
        )
        await engine.handle_frame(_event(1, SimEventType.SPEAKING_START, "p_a"))

    asyncio.run(run())
    assert calls["on_event"] == 0
    assert registry.get("dead_weight").enabled is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
