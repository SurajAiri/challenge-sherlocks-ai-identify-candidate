"""
FastAPI service — the HTTP-native sibling of cli.py.

No argparse, no console/dry-run mode. Three endpoints, all POST with the
same {"scenario_dir": ...} body shape (no DB/session state here, so the
scenario has to be named explicitly on every call - a bare slug isn't
enough on its own):

    POST /validate     -> is this scenario well-formed? (author tooling)
    POST /run          -> SSE stream of context/event frames (Engine-facing)
    POST /evaluation   -> grading/dashboard metadata (author/scoring tooling)

/validate and /evaluation are author/scoring-facing only and must never be
called by anything sitting in the Engine's live identification path -
/evaluation in particular returns ground_truth_participant_id, which is
the one piece of information the whole simulator design goes out of its
way to keep off the /run wire. /validate deliberately does NOT return
ground truth (even though it has it, from the compiled scenario) for the
same reason: a scenario-validity check has no business being a second
channel that leaks the answer.

/run compiles the scenario up front (so a broken scenario 4xx's
immediately, before the stream opens - same instinct as cli.py's `serve`
failing fast before the socket opens) and then streams context/event/
stream/error frames as Server-Sent Events, one JSON object per frame,
reusing the exact same `emit()` generator the CLI's `serve` command
uses. `stream` frames carry base64-encoded raw media bytes for a
currently-open webcam/audio/screenshare track - `event` frames for
those tracks (webcam_on/audio_stream_on/screenshare_start) carry only
lifecycle + codec metadata (width/height/fps, sample_rate/encoding),
never a file path.

Run with:
    uv run uvicorn simulator.api:app --host 0.0.0.0 --port 8080
(from inside src/, same convention as cli.py's flat imports)
"""

from __future__ import annotations

import dataclasses
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from simulator.compiler import compile_scenario
from simulator.emitter import emit
from simulator.validator import ValidationError

app = FastAPI(title="Sherlocks Simulator API", version="0.1.0")


class ScenarioRequest(BaseModel):
    scenario_dir: str
    # Optional per-request override of index.yml's controls.speed_multiplier.
    # Only meaningful for /run - /validate and /evaluation ignore it.
    # Deliberately NOT baked into compile_scenario()/its cache key: it's a
    # playback knob, not part of the scenario's identity (same reasoning
    # as ScenarioControls.speed_multiplier itself - see models.py), so
    # overriding it must never bust or fork the compiled-scenario cache.
    speed_multiplier: float | None = None


def _compile_or_4xx(scenario_dir: str):
    try:
        return compile_scenario(scenario_dir, driverName="espeak")
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(
            status_code=422, detail={"valid": False, "errors": e.errors}
        )


@app.post("/validate")
def validate_scenario(req: ScenarioRequest):
    """Author-facing sanity check only. Deliberately does NOT return
    ground_truth_participant_id, difficulty, or any other evaluation
    field - that's what /evaluation is for. Keeping this response
    minimal means there's only one endpoint anyone could accidentally
    wire an Engine up to and leak the answer through."""
    scenario = _compile_or_4xx(req.scenario_dir)
    return {
        "valid": True,
        "name": scenario.metadata.name,
        "slug": scenario.metadata.slug,
        "participants": list(scenario.participants.keys()),
        "timeline_events": len(scenario.timeline),
    }


@app.post("/evaluation")
def evaluation_scenario(req: ScenarioRequest):
    """Grading/dashboard metadata for a scenario: identity fields
    (name, slug, description) alongside everything under `evaluation`
    in index.yml (ground truth, difficulty, challenging points,
    expected evidence). This is the one endpoint allowed to return
    ground_truth_participant_id - callers are responsible for keeping
    it out of anything that talks to a live Engine.

    Reuses compile_scenario() (same as /validate and /run) rather than
    a second hand-rolled YAML parser, so there's exactly one place that
    decides what a scenario's fields mean - at the cost of a full
    compile (TTS/ffmpeg synthesis included) on a cold cache. In
    practice this is only slow the very first time any endpoint touches
    a given scenario; compiled output is cached under
    scenario_dir/.cache/ and reused by every endpoint after that,
    including this one.
    """
    scenario = _compile_or_4xx(req.scenario_dir)
    return {
        "name": scenario.metadata.name,
        "slug": scenario.metadata.slug,
        "description": scenario.metadata.description,
        "ground_truth_participant_id": scenario.evaluation.ground_truth_participant_id,
        "difficulty": scenario.evaluation.difficulty,
        "challenging_points": scenario.evaluation.challenging_points,
        "expected_evidence": scenario.evaluation.expected_evidence,
    }


def _sse(kind: str, payload) -> str:
    d = dataclasses.asdict(payload) if dataclasses.is_dataclass(payload) else payload
    return f"event: {kind}\ndata: {json.dumps(d)}\n\n"


@app.post("/run")
def run_scenario(req: ScenarioRequest):
    scenario = _compile_or_4xx(req.scenario_dir)  # fail fast, before streaming starts

    if req.speed_multiplier is not None:
        if req.speed_multiplier <= 0:
            raise HTTPException(
                status_code=422,
                detail={"valid": False, "errors": ["speed_multiplier must be > 0"]},
            )
        # Mutates the in-memory CompiledScenario only - the object just
        # came out of compile_scenario() for this request and is never
        # written back to the .cache/compiled.json cache, so this can't
        # leak into a different request that didn't ask for an override.
        scenario.controls.speed_multiplier = req.speed_multiplier

    async def stream():
        async for kind, payload in emit(scenario):
            yield _sse(kind, payload)

    return StreamingResponse(stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
