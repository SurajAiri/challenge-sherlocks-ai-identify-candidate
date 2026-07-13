# Sherlock dashboard (`apps/web`)

A testing/ops dashboard for the candidate-identification pipeline. It doesn't
run any identification logic itself - it exists to:

1. Let you load a scenario from the simulator and **see** the meeting it
   produces (participants joining, webcams, transcript, raw events) before
   trusting anything downstream.
2. Drive a run: start the simulator's `/run` stream, forward every frame to
   the Engine over WebSocket, and show whatever the Engine sends back.
3. Give the (not-yet-built) Engine a stable slot to plug into - the right-hand
   panel and the results page already parse a prediction message shape; wiring
   the real Engine up should only mean pointing an env var at it.

## Pages

- `/` - **Scenario library.** Add a scenario by directory path (the folder
  containing its `index.yml`); the dashboard calls the simulator's
  `/evaluation` endpoint to validate it and pull in its name, description,
  difficulty, and challenging points. Already-added paths are deduped, not
  re-added. Stored in `localStorage` (zustand `persist`), so it's local to
  your browser.
- `/session/[id]` - **Live meeting.** Start the run, watch participants join,
  webcams render frame-by-frame on canvas, transcript segments stream in
  speaker-attributed, and the raw event log (collapsible) for debugging. The
  right-hand Engine panel is a placeholder until an Engine is connected.
- `/session/[id]/result` - **Run summary / scoring.** Compares the Engine's
  last prediction against the scenario's ground truth (pulled from
  `/evaluation` at add-time, purely for scoring - see "What's a placeholder"
  below) plus a few run stats.

## Architecture

```
Browser  --fetch(POST)-->  /api/simulator/run  --fetch(POST)-->  Python simulator (/run, SSE)
   ^                              (Next.js server, same-origin;
   |                               avoids CORS entirely)
   |
   `--WebSocket------------------->  Engine (not built yet)
```

- `/api/simulator/{validate,evaluation,run}` are thin server-side proxies to
  the Python simulator (`SIMULATOR_BASE_URL`). The browser never talks to the
  simulator directly, so there's no CORS configuration needed on the FastAPI
  side.
- The simulator's `/run` endpoint is POST (scenario_dir lives in the body), so
  native `EventSource` (GET-only) can't be used - `lib/sse.ts` reads the
  `fetch()` `ReadableStream` by hand instead.
- Every SSE frame (`context` / `event` / `stream`) gets applied to
  `store/session-store.ts` **and** forwarded verbatim to the Engine over
  WebSocket (`lib/engine-client.ts`). The Engine socket auto-reconnects and
  never throws if nothing's listening yet.

## What's real vs. a placeholder right now

| Piece | Status |
|---|---|
| Scenario library, add/validate/dedupe | Real - talks to the simulator |
| Meeting grid, transcript, raw log | Real - driven by actual simulator SSE frames |
| Audio playback toggle | Real, but off by default (see below) |
| Engine connection, prediction panel | **Placeholder.** Connects to `NEXT_PUBLIC_ENGINE_WS_URL` and renders whatever comes back, but there's no Engine listening there yet. Everything shows `—` until there is. |
| Results/scoring page | **Placeholder-ish.** The ground-truth comparison is real (from `/evaluation`), but there's nothing to compare *to* until the Engine sends a prediction. |

### Audio playback

Stream chunks for `modality: "audio"` are raw `pcm_s16le` bytes with no
container (see `compiler.py`), so playback means buffering an utterance's
chunks and wrapping them in a WAV header (`lib/audio.ts`) once its matching
`audio_stream_off` arrives. This is real, but gated behind a toggle
(off by default) because buffering raw PCM for every utterance isn't free and
isn't needed to trust the pipeline - the frames drawing correctly and the
transcript text lining up is enough for that. Flip "Decode audio for
playback" on before starting a run to hear it.

Audio is joined to its transcript segment by the compiler's explicit
`track_id` / `data.audio_track_id` (see `compiler.py`), not by arrival
order - `audio_stream_off` for a given utterance is actually emitted
*before* its transcript_segment on the wire (it's auto-derived inline
while compiling `audio_stream_on`, before the loop reaches the
authored `transcript_segment` that follows by convention), so an
order-based "attach to whatever's most recent" heuristic would
misattach audio to the previous segment (or drop it) instead.
`session-store.ts` keeps a small `pendingAudioByTrackId` map to bridge
that gap regardless of which side resolves first.

## Setup

```bash
cd apps/web
pnpm install   # or from repo root: pnpm install
cp .env.local.example .env.local
pnpm dev
```

By default this expects:
- the simulator's FastAPI service at `http://localhost:8080` (see
  `apps/simulator` - run with `uv run uvicorn simulator.api:app --port 8080`)
- an Engine WebSocket at `ws://localhost:8090/ws` (fine to leave unset/unreachable for now)

## Assumptions

- One scenario runs at a time; there's a single global session store, reset
  each time you open a `/session/[id]` page. This is a testing tool for one
  developer at a time, not a multi-tenant app.
- `role_hint` (which participant is the candidate/interviewer/observer) is
  **never** shown anywhere in the live session UI - the simulator itself never
  puts it on the wire (see `compiler.py`), since guessing it is the whole
  point of the challenge. It only surfaces, as ground truth, on the results
  page after a run completes.
- The Engine's prediction message shape is a guess (`lib/types.ts`'s
  `engineMessageSchema`): `{ candidate_participant_id, confidence, reasoning,
  top_candidates?, t? }`. Field lookups in `session-store.ts` also accept a
  few alternate key names (`participant_id`, `candidate_id`, `score`, …) so a
  slightly different real shape degrades gracefully instead of breaking.
