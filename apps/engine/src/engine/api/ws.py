"""
WebSocket ingestion route.

One connection = one interview session = one `SessionEngine`. The
dashboard forwards every simulator SSE frame verbatim as `{kind,
payload}` JSON the moment it arrives (see `session-client.tsx`:
`engineSocketRef.current?.send(frame)`), so this route's only jobs are:

  1. accept the connection and spin up a fresh SessionEngine + heartbeat,
  2. parse each inbound message into a typed SimFrame (never trust the
     wire - a bad/partial frame must never kill the whole connection),
  3. hand it to the engine,
  4. push the engine's outbound EngineMessage JSON straight back down
     the same socket.

No auth, no session multiplexing, no reconnection/resume support yet -
this is the base input/output layer the task asked for first. Those
are the natural next layers once real identifiers exist and this needs
to survive a dashboard reconnect mid-interview.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from engine.core.schemas import parse_sim_frame
from engine.core.session_engine import SessionEngine

logger = logging.getLogger("engine.ws")

router = APIRouter()

# How often to push a snapshot even if nothing happened. Keeps the
# dashboard's confidence panel visibly "alive" through quiet stretches
# without depending on a new SimEvent to trigger it.
HEARTBEAT_INTERVAL_SECONDS = 5.0


@router.websocket("/ws")
async def engine_ws(websocket: WebSocket) -> None:
    await websocket.accept()

    async def send(payload: dict) -> None:
        await websocket.send_json(payload)

    engine = SessionEngine(send=send)
    heartbeat_task = asyncio.create_task(_heartbeat_loop(engine))

    try:
        while True:
            raw = await websocket.receive_text()
            await _handle_raw_message(engine, raw)
    except WebSocketDisconnect:
        logger.info("dashboard disconnected")
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task


async def _handle_raw_message(engine: SessionEngine, raw: str) -> None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("dropped unparseable message: %s", raw[:200])
        return

    try:
        frame = parse_sim_frame(parsed)
    except (ValidationError, ValueError) as exc:
        logger.warning("dropped invalid SimFrame (%s): %s", exc, raw[:200])
        return

    await engine.handle_frame(frame)


async def _heartbeat_loop(engine: SessionEngine) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        try:
            await engine.heartbeat()
        except Exception:
            logger.exception("heartbeat failed")
