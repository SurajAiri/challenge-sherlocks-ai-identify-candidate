"""
End-to-end SessionEngine test using a scenario shaped exactly like
`apps/simulator/scenarios-ref/demo_clean/index.yml`: candidate joins
under a device-like nickname ("MacBook Pro"), two interviewers drive
most of the early Q&A, a silent observer joins under an equally
device-like name ("iPhone"), and the candidate's real name is only
revealed via a `participant_update` near the end.

This does not go through the WebSocket - it drives `SessionEngine`
directly with the same typed frames the ws route would construct,
which is enough to exercise the full input -> identifiers -> evidence
-> belief -> output pipeline without needing a running server.

Run with: `uv run pytest` from `apps/engine/`.
"""
from __future__ import annotations

import asyncio

import pytest

from engine.core.identifiers.base import Identifier, IdentifierContext, IdentifierKind, IdentifierRunMode
from engine.core.identifiers.registry import IdentifierRegistry
from engine.core.schemas import (
    ContextFrame,
    SessionContext,
    SimEvent,
    SimEventFrame,
    SimEventType,
)
from engine.core.session_engine import SessionEngine


def _event(t: float, type_: SimEventType, participant_id: str | None, **data) -> SimEventFrame:
    return SimEventFrame(payload=SimEvent(t=t, type=type_, participant_id=participant_id, data=data))


async def _run_demo_clean_shaped_scenario() -> dict:
    messages: list[dict] = []

    async def send(payload: dict) -> None:
        messages.append(payload)

    engine = SessionEngine(send=send)

    frames: list = [
        ContextFrame(
            payload=SessionContext(
                candidate_name="Suraj Thapa",
                candidate_email="suraj.thapa@example.com",
                interviewer_names=["Alex Rivera", "Jordan Lee"],
                calendar_invite={"organizer": "Alex Rivera", "title": "Backend Engineer Interview - Suraj Thapa"},
                interview_schedule={"start": "2026-07-12T10:00:00", "duration_minutes": 30},
            )
        ),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_alex", display_name="Alex Rivera"),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_jordan", display_name="Jordan Lee"),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_mbp", display_name="MacBook Pro"),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_obs", display_name="iPhone"),
        # Alex asks the opening question.
        _event(2, SimEventType.SPEAKING_START, "p_alex"),
        _event(10, SimEventType.TRANSCRIPT_SEGMENT, "p_alex", text="Can you start by walking us through your background?"),
        _event(10, SimEventType.SPEAKING_END, "p_alex"),
        # Candidate answers at length.
        _event(11, SimEventType.SPEAKING_START, "p_mbp"),
        _event(35, SimEventType.TRANSCRIPT_SEGMENT, "p_mbp", text="Sure. I've spent the last four years building backend systems, mostly in Python and Go."),
        _event(35, SimEventType.SPEAKING_END, "p_mbp"),
        # Jordan asks a follow-up and requests a screenshare.
        _event(36, SimEventType.SPEAKING_START, "p_jordan"),
        _event(44, SimEventType.TRANSCRIPT_SEGMENT, "p_jordan", text="Great. Can you share your screen and walk us through your approach?"),
        _event(44, SimEventType.SPEAKING_END, "p_jordan"),
        _event(45, SimEventType.SCREENSHARE_START, "p_mbp"),
        _event(46, SimEventType.SPEAKING_START, "p_mbp"),
        _event(70, SimEventType.TRANSCRIPT_SEGMENT, "p_mbp", text="I'll start with a brute force approach, then optimize."),
        _event(70, SimEventType.SPEAKING_END, "p_mbp"),
        _event(71, SimEventType.SCREENSHARE_END, "p_mbp"),
        # Closing exchange, candidate finally states their real name.
        _event(72, SimEventType.SPEAKING_START, "p_alex"),
        _event(80, SimEventType.TRANSCRIPT_SEGMENT, "p_alex", text="Anything you'd like to ask us before we wrap up?"),
        _event(80, SimEventType.SPEAKING_END, "p_alex"),
        _event(81, SimEventType.SPEAKING_START, "p_mbp"),
        _event(92, SimEventType.TRANSCRIPT_SEGMENT, "p_mbp", text="Not right now, thank you. Oh, I'm Suraj by the way, sorry, I didn't introduce myself."),
        _event(92, SimEventType.SPEAKING_END, "p_mbp"),
        _event(93, SimEventType.PARTICIPANT_UPDATE, "p_mbp", display_name="Suraj Thapa"),
    ]

    for frame in frames:
        await engine.handle_frame(frame)

    return messages[-1]


def test_candidate_identified_despite_nickname_and_decoy_observer():
    final = asyncio.run(_run_demo_clean_shaped_scenario())

    assert final["possible_candidate_ids"] == ["p_mbp"]

    being_candidate = dict(final["probability_being_candidate"])
    not_candidate = dict(final["probability_not_being_candidate"])

    # Both interviewers and the silent, device-named observer must rank
    # below the candidate despite p_obs's name superficially looking
    # exactly as "device-like" as the candidate's.
    assert being_candidate["p_mbp"] > being_candidate["p_alex"]
    assert being_candidate["p_mbp"] > being_candidate["p_jordan"]
    assert being_candidate["p_mbp"] > being_candidate["p_obs"]

    # Interviewers should have accumulated meaningful "not candidate"
    # evidence from the name match against context.interviewer_names.
    assert not_candidate["p_alex"] > 0.5
    assert not_candidate["p_jordan"] > 0.5

    # Explainability trail is only attached to the reported candidate,
    # not to every participant on every message.
    assert final["evidence"]["p_mbp"]
    assert "p_alex" not in final["evidence"]


def test_no_confident_guess_before_any_evidence():
    async def send(payload: dict) -> None:
        send.messages.append(payload)  # type: ignore[attr-defined]

    send.messages = []  # type: ignore[attr-defined]

    async def run():
        engine = SessionEngine(send=send)
        await engine.handle_frame(_event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="Someone"))
        await engine.handle_frame(_event(0, SimEventType.PARTICIPANT_JOIN, "p_b", display_name="Someone Else"))

    asyncio.run(run())
    latest = send.messages[-1]  # type: ignore[attr-defined]
    # No name/context match, no speaking yet - the engine should say
    # "not sure" rather than guessing.
    assert latest["possible_candidate_ids"] == []


def test_one_time_identifier_is_never_invoked_off_the_continuous_bus():
    """A ONE_TIME identifier that (perhaps by copy-paste) also declares
    `listens_to` must NOT get `on_event` fired repeatedly - only
    `on_join`, exactly once. Regression test for a real bug where
    `_wire_continuous_identifiers` ignored `run_mode` entirely."""
    calls = {"on_join": 0, "on_event": 0}

    class OneTimeOnlyIdentifier(Identifier):
        id = "test_one_time"
        kind = IdentifierKind.INSTANT
        run_mode = IdentifierRunMode.ONE_TIME
        listens_to = frozenset({SimEventType.PARTICIPANT_UPDATE.value})

        async def on_join(self, participant_id: str, ctx: IdentifierContext) -> None:
            calls["on_join"] += 1

        async def on_event(self, event, ctx: IdentifierContext) -> None:
            calls["on_event"] += 1

    async def send(payload: dict) -> None:
        return None

    async def run():
        registry = IdentifierRegistry([OneTimeOnlyIdentifier()])
        engine = SessionEngine(send=send, registry=registry)
        await engine.handle_frame(_event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="A"))
        await engine.handle_frame(_event(1, SimEventType.PARTICIPANT_UPDATE, "p_a", display_name="A2"))
        await engine.handle_frame(_event(2, SimEventType.PARTICIPANT_UPDATE, "p_a", display_name="A3"))

    asyncio.run(run())
    assert calls == {"on_join": 1, "on_event": 0}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
