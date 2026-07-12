"""
FastAPI service — the HTTP-native sibling of cli.py.

No argparse, no console/dry-run mode. Two endpoints:

    POST /validate   {"scenario_dir": "scenarios/demo_clean"}
    POST /run        {"scenario_dir": "scenarios/demo_clean"}   -> SSE stream

Both take the same body shape. /run compiles the scenario up front (so a
broken scenario 4xx's immediately, before the stream opens - same instinct
as cli.py's `serve` failing fast before the socket opens) and then streams
context/event/error frames as Server-Sent Events, one JSON object per frame,
reusing the exact same `emit()` generator the CLI's `serve` command uses.

Run with:
    uv run uvicorn api:app --host 0.0.0.0 --port 8000
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


def _compile_or_4xx(scenario_dir: str):
    try:
        return compile_scenario(scenario_dir)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(
            status_code=422, detail={"valid": False, "errors": e.errors}
        )


@app.post("/validate")
def validate_scenario(req: ScenarioRequest):
    scenario = _compile_or_4xx(req.scenario_dir)
    return {
        "valid": True,
        "name": scenario.metadata.name,
        "slug": scenario.metadata.slug,
        "participants": list(scenario.participants.keys()),
        "timeline_events": len(scenario.timeline),
        "ground_truth_participant_id": scenario.metadata.ground_truth_participant_id,
    }


def _sse(kind: str, payload) -> str:
    d = dataclasses.asdict(payload) if dataclasses.is_dataclass(payload) else payload
    return f"event: {kind}\ndata: {json.dumps(d)}\n\n"


@app.post("/run")
def run_scenario(req: ScenarioRequest):
    scenario = _compile_or_4xx(req.scenario_dir)  # fail fast, before streaming starts

    async def stream():
        async for kind, payload in emit(scenario):
            yield _sse(kind, payload)

    return StreamingResponse(stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
