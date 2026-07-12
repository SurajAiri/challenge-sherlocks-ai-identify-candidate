"""
Emitter: plays back a CompiledScenario as an async event stream.

This owns exactly one clock: the event-scheduling clock (wall-time,
scaled by speed_multiplier). It does NOT decode audio/video frames —
webcam_on/audio_stream_on just hand the consumer a file path.
Whatever consumes it (a real Engine identifier) decides its own
sampling rate (e.g. 2fps for video, native rate for audio). This is
deliberate: the simulator's clock and a media codec's clock are
different concerns and must not be conflated into one "fps" knob.
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

from simulator.models import CompiledScenario, Event, EventType, SessionContext


async def emit(scenario: CompiledScenario) -> AsyncIterator[tuple[str, object]]:
    """
    Yields ("context", SessionContext) once, then ("event", Event) for
    each timeline event, sleeping in between to preserve relative timing
    (scaled by metadata.speed_multiplier).
    """
    yield ("context", scenario.context)

    start_wall = time.monotonic()
    speed = scenario.controls.speed_multiplier or 1.0

    for event in scenario.timeline:
        target_wall = start_wall + (event.t / speed)
        now = time.monotonic()
        if target_wall > now:
            await asyncio.sleep(target_wall - now)
        yield ("event", event)


def describe_event(event: Event, scenario: CompiledScenario) -> str:
    """Human-readable line, useful for a dry-run/demo consumer."""
    pname = ""
    if event.participant_id:
        p = scenario.participants.get(event.participant_id)
        pname = f" [{p.display_name if p else event.participant_id}]"
    extra = f" {event.data}" if event.data else ""
    return f"t={event.t:>6.1f}s  {event.type.value:<20}{pname}{extra}"
