# engine

The candidate-identification engine. One WebSocket connection in
(`/ws`), typed `SimFrame`s in, an `EngineMessage` prediction streamed
back after every meaningful frame. See
[docs/architecture.md](docs/architecture.md) for the full component
breakdown and how it maps to the system architecture diagram.

## Quick start

```bash
cd apps/engine
uv sync
uv run uvicorn main:api --reload --host 0.0.0.0 --port 8090
# or: npm run dev  (defined in package.json, same command)
```

The dashboard (`apps/web`) defaults to `ws://localhost:8090/ws` with no
env config needed - the port above is chosen to match that default. If
you run the engine elsewhere, set `NEXT_PUBLIC_ENGINE_WS_URL` in
`apps/web/.env.local`.

`GET /health` returns `{"status": "healthy"}` once the server is up.

## Running the tests

```bash
cd apps/engine
uv sync --group dev
uv run pytest -v
```

`tests/test_session_engine.py` drives `SessionEngine` directly (no
WebSocket needed) through a scenario shaped like
`apps/simulator/scenarios-ref/demo_clean/index.yml` - candidate joins
under a device-like nickname, a silent observer joins under an equally
device-like name, two interviewers drive the early Q&A, the candidate's
real name only surfaces near the end - and asserts the candidate still
comes out on top with the interviewers/observer ranked below, plus that
the engine reports "not sure yet" rather than guessing before any
evidence exists.

## Adding a new identifier

1. Add a class in `src/engine/identifiers/` subclassing
   `engine.core.identifiers.base.Identifier`. Set `id`, `weight`, `kind`
   (`INSTANT`/`TEMPORAL`), `run_mode` (`ONE_TIME`/`CONTINUOUS`/`BOTH`),
   and `listens_to` (a set of `SimEventType` values, or `{"*"}`).
2. Override `on_join()` and/or `on_event()`, call
   `await self.emit(ctx, participant_id=..., signal=..., direction=...,
   strength=..., reasoning=..., t=...)` whenever you have something to
   say about a participant.
3. Register it in `default_registry()`
   (`src/engine/core/identifiers/registry.py`). Nothing else in the
   engine needs to change - the event bus wiring, evidence normalization,
   and belief accumulation are all generic.

An identifier never writes to the state store directly and never talks
to another identifier directly; all coordination happens through
Evidence, which is what keeps each one independently
pluggable/testable/removable.

## Assumptions

- The dashboard is the only client; the WebSocket forwards frames
  verbatim and expects `EngineMessage` JSON back on the same connection
  (see `apps/web/src/lib/engine-client.ts` /
  `apps/web/src/lib/types.ts`).
- One WebSocket connection = one interview session. No
  multi-session-per-connection multiplexing, no auth, no
  reconnect/resume yet - noted as a next step, not silently assumed away.
- `StreamFrame.data` (base64 audio/video/screenshare chunks) is tracked
  for liveness/volume only at this layer; decoding pixels/audio samples
  is left to identifiers that need it (deepfake CV, voice analysis,
  etc.), which are future pluggable additions, not part of this base
  layer.
- A handful of participants per session (typical interview call size).
  `belief_engine.softmax()` recomputes over the full participant pool on
  every evidence update, which is intentionally simple and would need
  revisiting for calls with dozens of participants.

## Known limitations / next steps

- The four shipped identifiers (`name_match`, `speaking_share`,
  `qa_pattern`, `screenshare_heuristic`) are heuristics meant to prove
  the pipeline end-to-end, not the final signal set. `qa_pattern`'s
  "ends with `?`" question detector in particular is a placeholder for
  an LLM-based or trained classification pass.
- `probability_not_candidate` / `belief_engine.is_eliminated()` isn't yet
  wired into any scheduling decision (e.g. skipping expensive
  video/audio identifiers for a participant already effectively ruled
  out) - the hook exists, the pruning behavior doesn't yet.
- No persistence: engine state lives only for the lifetime of the
  WebSocket connection. A dashboard reconnect mid-interview currently
  starts a fresh `SessionEngine` with no memory of what came before.
