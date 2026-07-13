"""
Regression tests for the /run speed_multiplier override.

Stubs out _compile_or_4xx so this doesn't need a real scenario dir /
ffmpeg / TTS on disk - only checks that (a) a provided speed_multiplier
overrides the compiled scenario's controls.speed_multiplier before
emit() runs, (b) omitting it leaves the authored value untouched, and
(c) a non-positive override is rejected with 422 rather than silently
producing a zero/negative-speed (i.e. hung or backwards) playback.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from simulator import api as api_mod
from simulator.models import (
    CompiledScenario, Participant, ScenarioControls, ScenarioEvaluation,
    ScenarioMetadata, SessionContext,
)


def _fake_scenario(speed_multiplier: float = 1.0) -> CompiledScenario:
    return CompiledScenario(
        metadata=ScenarioMetadata(name="t", slug="t"),
        controls=ScenarioControls(speed_multiplier=speed_multiplier),
        evaluation=ScenarioEvaluation(),
        context=SessionContext(
            calendar_invite={}, interview_schedule={}, interviewer_names=[],
            candidate_name="C", candidate_email="c@example.com",
        ),
        participants={"p1": Participant(participant_id="p1", display_name="P1")},
        timeline=[],
        scenario_dir="unused",
    )


def test_run_without_override_keeps_authored_speed(monkeypatch):
    seen = {}

    def fake_compile_or_4xx(scenario_dir):
        scenario = _fake_scenario(speed_multiplier=3.0)
        seen["scenario"] = scenario
        return scenario

    monkeypatch.setattr(api_mod, "_compile_or_4xx", fake_compile_or_4xx)
    client = TestClient(api_mod.app)
    resp = client.post("/run", json={"scenario_dir": "unused"})
    assert resp.status_code == 200
    assert seen["scenario"].controls.speed_multiplier == 3.0


def test_run_with_override_replaces_authored_speed(monkeypatch):
    seen = {}

    def fake_compile_or_4xx(scenario_dir):
        scenario = _fake_scenario(speed_multiplier=1.0)
        seen["scenario"] = scenario
        return scenario

    monkeypatch.setattr(api_mod, "_compile_or_4xx", fake_compile_or_4xx)
    client = TestClient(api_mod.app)
    resp = client.post("/run", json={"scenario_dir": "unused", "speed_multiplier": 8.0})
    assert resp.status_code == 200
    assert seen["scenario"].controls.speed_multiplier == 8.0


def test_run_rejects_non_positive_speed_override(monkeypatch):
    monkeypatch.setattr(api_mod, "_compile_or_4xx", lambda scenario_dir: _fake_scenario())
    client = TestClient(api_mod.app)
    for bad in (0, -1.0):
        resp = client.post("/run", json={"scenario_dir": "unused", "speed_multiplier": bad})
        assert resp.status_code == 422
