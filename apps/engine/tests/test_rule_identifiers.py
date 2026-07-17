"""
Tests for the three new rule-based identifiers:
`silent_observer.py`, `host_organizer.py`, `email_identity.py`.

Same style as the existing identifier-adjacent tests: drive a
SessionEngine directly with typed frames through a minimal
single-identifier registry, and assert on the resulting EngineMessage.

Run with: `uv run pytest` from `apps/engine/`.
"""
from __future__ import annotations

import asyncio

import pytest

from engine.core.identifiers.registry import IdentifierRegistry
from engine.core.schemas import ContextFrame, SessionContext, SimEvent, SimEventFrame, SimEventType
from engine.core.session_engine import SessionEngine
from engine.identifiers.email_identity import EmailIdentityIdentifier
from engine.identifiers.host_organizer import HostOrganizerExclusionIdentifier
from engine.identifiers.silent_observer import (
    MIN_PRESENT_SECONDS_BEFORE_SIGNAL,
    SATURATION_SECONDS,
    SilentObserverIdentifier,
)


def _event(t: float, type_: SimEventType, participant_id: str | None, **data) -> SimEventFrame:
    return SimEventFrame(payload=SimEvent(t=t, type=type_, participant_id=participant_id, data=data))


def _context(**overrides) -> ContextFrame:
    defaults = dict(
        candidate_name="Suraj Thapa",
        candidate_email="suraj.thapa@example.com",
        interviewer_names=["Alex Rivera"],
        calendar_invite={"organizer": "Alex Rivera"},
    )
    defaults.update(overrides)
    return ContextFrame(payload=SessionContext(**defaults))


async def _drive(frames, registry) -> list[dict]:
    messages: list[dict] = []

    async def send(payload: dict) -> None:
        messages.append(payload)

    engine = SessionEngine(send=send, registry=registry)
    for frame in frames:
        await engine.handle_frame(frame)
    return messages


# -- SilentObserverIdentifier -------------------------------------------------


def test_silent_observer_does_not_fire_before_time_floor():
    registry = IdentifierRegistry([SilentObserverIdentifier()])
    frames = [
        _context(),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="iPhone"),
        _event(MIN_PRESENT_SECONDS_BEFORE_SIGNAL - 5, SimEventType.PARTICIPANT_JOIN, "p_b", display_name="Alex"),
    ]
    messages = asyncio.run(_drive(frames, registry))
    final = messages[-1]
    assert dict(final["probability_not_being_candidate"]).get("p_a", 0.0) == 0.0


def test_silent_observer_fires_after_time_floor_with_no_activity():
    registry = IdentifierRegistry([SilentObserverIdentifier()])
    frames = [
        _context(),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="iPhone"),
        # Trigger events attributed to someone else - silent_observer
        # listens on "*" and re-evaluates every present participant
        # regardless of who the triggering event names.
        _event(MIN_PRESENT_SECONDS_BEFORE_SIGNAL + 5, SimEventType.PARTICIPANT_JOIN, "p_b", display_name="Alex"),
    ]
    messages = asyncio.run(_drive(frames, registry))
    final = messages[-1]
    assert dict(final["probability_not_being_candidate"])["p_a"] > 0.0


def test_silent_observer_never_fires_for_participant_who_spoke():
    registry = IdentifierRegistry([SilentObserverIdentifier()])
    frames = [
        _context(),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="Suraj"),
        _event(1, SimEventType.SPEAKING_START, "p_a"),
        _event(2, SimEventType.SPEAKING_END, "p_a"),
        _event(MIN_PRESENT_SECONDS_BEFORE_SIGNAL + 5, SimEventType.PARTICIPANT_JOIN, "p_b", display_name="Alex"),
    ]
    messages = asyncio.run(_drive(frames, registry))
    final = messages[-1]
    assert dict(final["probability_not_being_candidate"]).get("p_a", 0.0) == 0.0


def test_silent_observer_strength_saturates_and_does_not_exceed_max():
    registry = IdentifierRegistry([SilentObserverIdentifier()])
    frames = [
        _context(),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="iPhone"),
        _event(SATURATION_SECONDS + 100, SimEventType.PARTICIPANT_JOIN, "p_b", display_name="Alex"),
    ]
    messages = asyncio.run(_drive(frames, registry))
    final = messages[-1]
    # Saturated strength still produces a valid (<=1) probability, not
    # something that blew past a bound.
    assert 0.0 < dict(final["probability_not_being_candidate"])["p_a"] <= 1.0


# -- HostOrganizerExclusionIdentifier -----------------------------------------


def test_host_organizer_exclusion_fires_on_organizer_name_match():
    registry = IdentifierRegistry([HostOrganizerExclusionIdentifier()])
    frames = [
        _context(calendar_invite={"organizer": "Alex Rivera"}),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="Alex Rivera"),
    ]
    messages = asyncio.run(_drive(frames, registry))
    final = messages[-1]
    assert dict(final["probability_not_being_candidate"])["p_a"] > 0.0


def test_host_organizer_exclusion_silent_when_no_organizer_context():
    registry = IdentifierRegistry([HostOrganizerExclusionIdentifier()])
    frames = [
        _context(calendar_invite={}),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="Alex Rivera"),
    ]
    messages = asyncio.run(_drive(frames, registry))
    final = messages[-1]
    assert dict(final["probability_not_being_candidate"]).get("p_a", 0.0) == 0.0


def test_host_organizer_exclusion_does_not_fire_on_unrelated_name():
    registry = IdentifierRegistry([HostOrganizerExclusionIdentifier()])
    frames = [
        _context(calendar_invite={"organizer": "Alex Rivera"}),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="MacBook Pro"),
    ]
    messages = asyncio.run(_drive(frames, registry))
    final = messages[-1]
    assert dict(final["probability_not_being_candidate"]).get("p_a", 0.0) == 0.0


def test_host_organizer_exclusion_fires_on_updated_display_name():
    registry = IdentifierRegistry([HostOrganizerExclusionIdentifier()])
    frames = [
        _context(calendar_invite={"organizer": "Jordan Lee"}),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="iPad"),
        _event(5, SimEventType.PARTICIPANT_UPDATE, "p_a", display_name="Jordan Lee"),
    ]
    messages = asyncio.run(_drive(frames, registry))
    final = messages[-1]
    assert dict(final["probability_not_being_candidate"])["p_a"] > 0.0


# -- EmailIdentityIdentifier ---------------------------------------------------


def test_email_identity_fires_on_explicit_email_field_match():
    registry = IdentifierRegistry([EmailIdentityIdentifier()])
    frames = [
        _context(candidate_email="suraj.thapa@example.com"),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="MacBook Pro", email="suraj.thapa@example.com"),
    ]
    messages = asyncio.run(_drive(frames, registry))
    final = messages[-1]
    assert dict(final["probability_being_candidate"])["p_a"] > 0.0


def test_email_identity_fires_on_email_embedded_in_display_name():
    registry = IdentifierRegistry([EmailIdentityIdentifier()])
    frames = [
        _context(candidate_email="suraj.thapa@example.com"),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="Suraj Thapa <suraj.thapa@example.com>"),
    ]
    messages = asyncio.run(_drive(frames, registry))
    final = messages[-1]
    assert dict(final["probability_being_candidate"])["p_a"] > 0.0


def test_email_identity_silent_when_no_email_anywhere():
    registry = IdentifierRegistry([EmailIdentityIdentifier()])
    frames = [
        _context(candidate_email="suraj.thapa@example.com"),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="MacBook Pro"),
    ]
    messages = asyncio.run(_drive(frames, registry))
    final = messages[-1]
    assert dict(final["probability_being_candidate"]).get("p_a", 0.0) == 0.0


def test_email_identity_does_not_fire_on_mismatched_email():
    registry = IdentifierRegistry([EmailIdentityIdentifier()])
    frames = [
        _context(candidate_email="suraj.thapa@example.com"),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="Someone", email="someone.else@example.com"),
    ]
    messages = asyncio.run(_drive(frames, registry))
    final = messages[-1]
    assert dict(final["probability_being_candidate"]).get("p_a", 0.0) == 0.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
