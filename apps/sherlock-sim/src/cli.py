"""
Usage:
    uv run src/cli.py validate scenarios/demo_clean
    uv run src/cli.py run scenarios/demo_clean

`run` is a dry-run consumer (prints events as they'd arrive) — this is
the reference implementation of what your Engine's adapter should do
instead: subscribe to `emit()` and route events to the Event Bus.
"""
from __future__ import annotations

import asyncio
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


async def _run(scenario_dir: str) -> None:
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
    asyncio.run(_run(scenario_dir))


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    action, scenario_dir = sys.argv[1], sys.argv[2]
    if action == "validate":
        cmd_validate(scenario_dir)
    elif action == "run":
        cmd_run(scenario_dir)
    else:
        print(f"unknown action '{action}'. use 'validate' or 'run'.")
        sys.exit(1)


if __name__ == "__main__":
    main()
