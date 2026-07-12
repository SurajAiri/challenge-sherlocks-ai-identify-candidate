"""
Emitter: plays back a CompiledScenario as an async event stream.

This owns exactly one clock: the event-scheduling clock (wall-time,
scaled by speed_multiplier). Timestamps for every entry - discrete
events AND media stream chunks - are fully resolved at compile time
(see compiler.py), so this stays a single sequential walker: sleep to
the next `t`, yield, repeat. No concurrency, no per-track producer
tasks - the compiler already interleaved everything into one sorted
list, exactly the way join/leave/speaking events from different
participants were always interleaved before chunks existed.

Two kinds go out on the wire (three counting "context"):
  - ("event", Event)        - discrete state changes: join/leave,
    webcam_on/off (marker + track metadata only, no path),
    transcript_segment, etc. Sparse, low frequency.
  - ("stream", StreamFrame) - one already-open track's raw media
    payload (base64-encoded bytes). Dense, high frequency, always
    falls inside a currently-open on/off window for its
    participant_id+modality. Bytes are read lazily, right here, from
    the StreamChunk's local source_path/offset/length - that local
    path/offset never leaves this process; only the resulting bytes do.
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import AsyncIterator

from simulator.models import CompiledScenario, Event, EventType, StreamChunk, StreamFrame


def _read_chunk_bytes(chunk: StreamChunk) -> bytes:
    with open(chunk.source_path, "rb") as f:
        f.seek(chunk.byte_offset)
        return f.read() if chunk.byte_length is None else f.read(chunk.byte_length)


async def emit(scenario: CompiledScenario) -> AsyncIterator[tuple[str, object]]:
    """
    Yields ("context", SessionContext) once, then ("event", Event) or
    ("stream", StreamFrame) for each timeline entry in order, sleeping
    in between to preserve relative timing (scaled by
    metadata.speed_multiplier).
    """
    yield ("context", scenario.context)

    start_wall = time.monotonic()
    speed = scenario.controls.speed_multiplier or 1.0

    for entry in scenario.timeline:
        target_wall = start_wall + (entry.t / speed)
        now = time.monotonic()
        if target_wall > now:
            await asyncio.sleep(target_wall - now)

        if isinstance(entry, StreamChunk):
            frame = StreamFrame(
                t=entry.t,
                participant_id=entry.participant_id,
                modality=entry.modality,
                seq=entry.seq,
                data=base64.b64encode(_read_chunk_bytes(entry)).decode("ascii"),
            )
            yield ("stream", frame)
        else:
            yield ("event", entry)


def describe_event(event: Event, scenario: CompiledScenario) -> str:
    """Human-readable line, useful for a dry-run/demo consumer."""
    pname = ""
    if event.participant_id:
        p = scenario.participants.get(event.participant_id)
        pname = f" [{p.display_name if p else event.participant_id}]"
    extra = f" {event.data}" if event.data else ""
    return f"t={event.t:>6.1f}s  {event.type.value:<20}{pname}{extra}"


def describe_stream_frame(frame: StreamFrame, scenario: CompiledScenario) -> str:
    """Human-readable line for a stream chunk - deliberately doesn't
    print the base64 payload itself (useless noise for a dry-run), just
    which track it belongs to and how big it is."""
    p = scenario.participants.get(frame.participant_id)
    pname = p.display_name if p else frame.participant_id
    nbytes = len(frame.data) * 3 // 4  # approx decoded size from base64 length
    return (
        f"t={frame.t:>6.1f}s  stream:{frame.modality:<11} [{pname}] "
        f"seq={frame.seq} (~{nbytes}B)"
    )
