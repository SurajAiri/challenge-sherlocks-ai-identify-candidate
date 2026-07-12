"""
Usage:
    uv run src/cli.py validate scenarios/demo_clean
    uv run src/cli.py run scenarios/demo_clean
    uv run src/cli.py serve scenarios/demo_clean [--host 0.0.0.0] [--port 8765]

`run` is a human-readable dry-run (console output) for debugging a
scenario. `serve` is the real interface: the Engine connects over a
websocket and receives the same events as newline-free JSON messages -
this is what a real adapter's wire format should look like, so the
Engine never needs a special code path for "talking to the simulator".
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys

from compiler import compile_scenario
from emitter import describe_event, emit
from validator import ValidationError


def cmd_validate(scenario_dir: str) -> None:
    try:
        scenario = compile_scenario(scenario_dir)
    except ValidationError as e:
        print(f"INVALID scenario at {scenario_dir}:")
        print(e)
        sys.exit(1)
    print(f"VALID: '{scenario.metadata.name}' ({scenario.metadata.slug})")
    print(f"  participants: {list(scenario.participants.keys())}")
    print(f"  timeline events: {len(scenario.timeline)}")
    print(f"  ground truth: {scenario.metadata.ground_truth_participant_id}")


async def _run_console(scenario_dir: str) -> None:
    scenario = compile_scenario(scenario_dir)
    print(f"--- running '{scenario.metadata.name}' "
          f"(speed={scenario.metadata.speed_multiplier}x) ---\n")
    async for kind, payload in emit(scenario):
        if kind == "context":
            print(f"[SESSION START] candidate={payload.candidate_name} "
                  f"<{payload.candidate_email}> interviewers={payload.interviewer_names}")
        else:
            print(describe_event(payload, scenario))


def cmd_run(scenario_dir: str) -> None:
    asyncio.run(_run_console(scenario_dir))


def _to_wire(kind: str, payload) -> str:
    """SessionContext / Event -> JSON string for the websocket wire."""
    d = dataclasses.asdict(payload)
    return json.dumps({"kind": kind, "payload": d})


async def _serve(scenario_dir: str, host: str, port: int) -> None:
    import websockets

    async def handler(websocket):
        # Each connecting client gets its own fresh run of the scenario
        # from t=0 - this simulator models one meeting session, and a
        # client connecting is that client "joining" the session feed.
        try:
            scenario = compile_scenario(scenario_dir)
        except ValidationError as e:
            await websocket.send(json.dumps({"kind": "error", "payload": str(e)}))
            return
        print(f"[serve] client connected, streaming '{scenario.metadata.name}'")
        async for kind, payload in emit(scenario):
            await websocket.send(_to_wire(kind, payload))
        print("[serve] scenario complete, closing")

    async with websockets.serve(handler, host, port):
        print(f"[serve] listening on ws://{host}:{port} for scenario at {scenario_dir}")
        await asyncio.Future()  # run forever


def cmd_serve(scenario_dir: str, host: str, port: int) -> None:
    # fail fast on a broken scenario before even opening the socket
    compile_scenario(scenario_dir)
    asyncio.run(_serve(scenario_dir, host, port))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)

    p_validate = sub.add_parser("validate")
    p_validate.add_argument("scenario_dir")

    p_run = sub.add_parser("run")
    p_run.add_argument("scenario_dir")

    p_serve = sub.add_parser("serve")
    p_serve.add_argument("scenario_dir")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8765)

    args = parser.parse_args()

    if args.action == "validate":
        cmd_validate(args.scenario_dir)
    elif args.action == "run":
        cmd_run(args.scenario_dir)
    elif args.action == "serve":
        cmd_serve(args.scenario_dir, args.host, args.port)


if __name__ == "__main__":
    main()
