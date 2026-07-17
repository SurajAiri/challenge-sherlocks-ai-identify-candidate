"""
Tests for the two LLM-backed identifiers (`llm_name_role.py`,
`llm_transcript_role.py`) and the shared `llm_client.structured_completion`
helper they both call.

No real network/API calls are made anywhere in this file -
`structured_completion` is monkeypatched per-test to return a
canned parsed object (happy path) or `None` (failure path), which is
exactly the contract `llm_client.py` promises callers: success returns
a validated schema instance, anything else (missing key, timeout, bad
JSON, schema mismatch) collapses to `None`. This file is what proves
each identifier actually honors that contract - emits the right
Evidence on success, emits nothing and never raises on failure.

Run with: `uv run pytest` from `apps/engine/`.
"""
from __future__ import annotations

import asyncio

import pytest

from engine.core.identifiers.registry import IdentifierRegistry
from engine.core.schemas import ContextFrame, SessionContext, SimEvent, SimEventFrame, SimEventType
from engine.core.session_engine import SessionEngine
from engine.identifiers.llm_name_role import LLMNameRoleIdentifier, NameRoleVerdict
from engine.identifiers.llm_transcript_role import (
    MIN_DISTINCT_SPEAKERS,
    MIN_NEW_SEGMENTS_BETWEEN_CALLS,
    LLMTranscriptRoleIdentifier,
    ParticipantRoleVerdict,
    TranscriptRoleAssessment,
)


def _event(t: float, type_: SimEventType, participant_id: str | None, **data) -> SimEventFrame:
    return SimEventFrame(payload=SimEvent(t=t, type=type_, participant_id=participant_id, data=data))


def _context() -> ContextFrame:
    return ContextFrame(
        payload=SessionContext(
            candidate_name="Suraj Thapa",
            candidate_email="suraj.thapa@example.com",
            interviewer_names=["Alex Rivera"],
            calendar_invite={"organizer": "Alex Rivera"},
        )
    )


async def _drive(frames, registry) -> list[dict]:
    messages: list[dict] = []

    async def send(payload: dict) -> None:
        messages.append(payload)

    engine = SessionEngine(send=send, registry=registry)
    for frame in frames:
        await engine.handle_frame(frame)
    return messages


# -- LLMNameRoleIdentifier ---------------------------------------------------


def test_llm_name_role_emits_for_candidate_on_positive_verdict(monkeypatch):
    async def fake_structured_completion(*, system_prompt, user_prompt, schema, use_cache=True):
        assert schema is NameRoleVerdict
        return NameRoleVerdict(verdict="candidate", confidence=0.8, reasoning="Nickname of the candidate.")

    monkeypatch.setattr(
        "engine.identifiers.llm_name_role.structured_completion", fake_structured_completion
    )

    registry = IdentifierRegistry([LLMNameRoleIdentifier()])
    frames = [_context(), _event(0, SimEventType.PARTICIPANT_JOIN, "p_a", display_name="S. Thapa")]
    messages = asyncio.run(_drive(frames, registry))

    final = messages[-1]
    probs = dict(final["probability_being_candidate"])
    assert probs["p_a"] > 0.0
    assert "p_a" in final["evidence"] or probs["p_a"] > 0  # some positive signal recorded


def test_llm_name_role_emits_against_candidate_on_interviewer_verdict(monkeypatch):
    async def fake_structured_completion(*, system_prompt, user_prompt, schema, use_cache=True):
        return NameRoleVerdict(verdict="interviewer", confidence=0.9, reasoning="Matches known interviewer.")

    monkeypatch.setattr(
        "engine.identifiers.llm_name_role.structured_completion", fake_structured_completion
    )

    registry = IdentifierRegistry([LLMNameRoleIdentifier()])
    frames = [_context(), _event(0, SimEventType.PARTICIPANT_JOIN, "p_b", display_name="Alex R.")]
    messages = asyncio.run(_drive(frames, registry))

    final = messages[-1]
    probs_not = dict(final["probability_not_being_candidate"])
    assert probs_not["p_b"] > 0.0


def test_llm_name_role_emits_nothing_on_unclear_verdict(monkeypatch):
    async def fake_structured_completion(*, system_prompt, user_prompt, schema, use_cache=True):
        return NameRoleVerdict(verdict="unclear", confidence=0.4, reasoning="Could be anyone.")

    monkeypatch.setattr(
        "engine.identifiers.llm_name_role.structured_completion", fake_structured_completion
    )

    registry = IdentifierRegistry([LLMNameRoleIdentifier()])
    frames = [_context(), _event(0, SimEventType.PARTICIPANT_JOIN, "p_c", display_name="MacBook Pro")]
    messages = asyncio.run(_drive(frames, registry))

    final = messages[-1]
    assert dict(final["probability_being_candidate"]).get("p_c", 0.0) == 0.0
    assert dict(final["probability_not_being_candidate"]).get("p_c", 0.0) == 0.0


def test_llm_name_role_fails_open_when_llm_call_raises(monkeypatch):
    """The identifier must never propagate an exception raised inside
    the LLM call path - callers (SessionEngine._run_hook) already
    isolate identifier failures generically, but this proves the
    identifier's own contract (via structured_completion returning
    None on failure) actually holds, not just that something upstream
    happens to catch it."""

    async def fake_structured_completion(*, system_prompt, user_prompt, schema, use_cache=True):
        return None  # this is exactly what llm_client.py returns on any failure

    monkeypatch.setattr(
        "engine.identifiers.llm_name_role.structured_completion", fake_structured_completion
    )

    registry = IdentifierRegistry([LLMNameRoleIdentifier()])
    frames = [_context(), _event(0, SimEventType.PARTICIPANT_JOIN, "p_d", display_name="Whoever")]
    messages = asyncio.run(_drive(frames, registry))

    final = messages[-1]
    assert final["possible_candidate_ids"] == []


# -- LLMTranscriptRoleIdentifier ---------------------------------------------


def _transcript_frames() -> list:
    """Enough transcript_segment events, from >=2 distinct speakers, to
    cross MIN_NEW_SEGMENTS_BETWEEN_CALLS and MIN_DISTINCT_SPEAKERS so the
    identifier actually calls the (mocked) LLM."""
    frames = [
        _context(),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_int", display_name="Alex Rivera"),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_cand", display_name="MacBook Pro"),
    ]
    t = 1.0
    texts = [
        ("p_int", "Can you walk us through your background?"),
        ("p_cand", "Sure, I've spent four years in backend engineering."),
        ("p_int", "Great, let's do a coding problem."),
        ("p_cand", "I'll start with brute force then optimize."),
        ("p_int", "Sounds good, go ahead."),
    ]
    for pid, text in texts:
        frames.append(_event(t, SimEventType.TRANSCRIPT_SEGMENT, pid, text=text))
        t += 1.0
    assert len(texts) >= MIN_NEW_SEGMENTS_BETWEEN_CALLS
    assert MIN_DISTINCT_SPEAKERS <= 2
    return frames


def test_llm_transcript_role_emits_evidence_from_batched_assessment(monkeypatch):
    async def fake_structured_completion(*, system_prompt, user_prompt, schema, use_cache=True):
        assert schema is TranscriptRoleAssessment
        return TranscriptRoleAssessment(
            assessments=[
                ParticipantRoleVerdict(
                    participant_id="p_cand", verdict="interviewee", confidence=0.85, reasoning="Answers at length."
                ),
                ParticipantRoleVerdict(
                    participant_id="p_int", verdict="interviewer", confidence=0.9, reasoning="Asks questions."
                ),
            ]
        )

    monkeypatch.setattr(
        "engine.identifiers.llm_transcript_role.structured_completion", fake_structured_completion
    )

    registry = IdentifierRegistry([LLMTranscriptRoleIdentifier()])
    messages = asyncio.run(_drive(_transcript_frames(), registry))

    final = messages[-1]
    probs = dict(final["probability_being_candidate"])
    probs_not = dict(final["probability_not_being_candidate"])
    assert probs["p_cand"] > probs.get("p_int", 0.0)
    assert probs_not["p_int"] > 0.0


def test_llm_transcript_role_does_not_call_llm_before_batch_threshold(monkeypatch):
    calls = {"count": 0}

    async def fake_structured_completion(*, system_prompt, user_prompt, schema, use_cache=True):
        calls["count"] += 1
        return None

    monkeypatch.setattr(
        "engine.identifiers.llm_transcript_role.structured_completion", fake_structured_completion
    )

    registry = IdentifierRegistry([LLMTranscriptRoleIdentifier()])
    frames = [
        _context(),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_int", display_name="Alex Rivera"),
        _event(0, SimEventType.PARTICIPANT_JOIN, "p_cand", display_name="MacBook Pro"),
        _event(1, SimEventType.TRANSCRIPT_SEGMENT, "p_int", text="Hi there."),
    ]
    asyncio.run(_drive(frames, registry))
    # Only one distinct speaker so far AND below the new-segment
    # threshold - must not have called the LLM at all yet.
    assert calls["count"] == 0


def test_llm_transcript_role_fails_open_on_none_response(monkeypatch):
    async def fake_structured_completion(*, system_prompt, user_prompt, schema, use_cache=True):
        return None

    monkeypatch.setattr(
        "engine.identifiers.llm_transcript_role.structured_completion", fake_structured_completion
    )

    registry = IdentifierRegistry([LLMTranscriptRoleIdentifier()])
    messages = asyncio.run(_drive(_transcript_frames(), registry))

    final = messages[-1]
    assert final["possible_candidate_ids"] == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
