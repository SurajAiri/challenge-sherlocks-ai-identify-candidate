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

    assert final["candidate_participant_id"] == "p_mbp"
    assert final["confidence"] is not None and final["confidence"] > 0.5

    by_id = {c["participant_id"]: c for c in final["top_candidates"]}
    # Both interviewers and the silent, device-named observer must rank
    # below the candidate despite p_obs's name superficially looking
    # exactly as "device-like" as the candidate's.
    assert by_id["p_mbp"]["confidence"] > by_id["p_alex"]["confidence"]
    assert by_id["p_mbp"]["confidence"] > by_id["p_jordan"]["confidence"]
    assert by_id["p_mbp"]["confidence"] > by_id["p_obs"]["confidence"]

    # Interviewers should have accumulated meaningful "not candidate"
    # evidence from the name match against context.interviewer_names.
    assert by_id["p_alex"]["probability_not_candidate"] > 0.5
    assert by_id["p_jordan"]["probability_not_candidate"] > 0.5


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
    assert latest["candidate_participant_id"] is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
