# Scenario Simulator

Emits meeting events (join/leave, webcam, screenshare, speaking/transcript,
audio streams) from a hand-authored `index.yml`, timed and shaped exactly
like a real Meet/Zoom/Teams adapter would deliver them to the Engine —
so the Engine has no special code path for "talking to the simulator".

## Requires
- Python 3.12+, `uv`
- `ffmpeg` / `ffprobe` on PATH
- `espeak-ng` on PATH (offline TTS backend for `pyttsx3`)
  - Debian/Ubuntu: `apt-get install espeak-ng espeak`

## Usage
```
uv run src/cli.py validate scenarios/demo_clean
uv run src/cli.py run scenarios/demo_clean          # console dry-run
uv run src/cli.py serve scenarios/demo_clean         # websocket, JSON wire format
```
`serve` is the real interface: it opens `ws://0.0.0.0:8765` and streams
`{"kind": "context"|"event", "payload": {...}}` JSON messages — this is
what the Engine should actually connect to.

There's also an HTTP/SSE sibling, `api.py` (`uv run uvicorn api:app`), with
three POST endpoints, all taking `{"scenario_dir": "scenarios/demo_clean"}`:
`/validate` (author sanity check, no grading fields returned), `/run` (SSE
stream, same wire contract as `serve`), and `/evaluation` (grading/dashboard
metadata: ground truth, difficulty, challenging points, expected evidence —
never call this from anything wired to a live Engine).

## Wire protocol: `context` / `event` / `stream`
Both `serve` and `/run` emit three message kinds, in this shape:
`{"kind": "context"|"event"|"stream", "payload": {...}}`.

- **`context`** (once, first) — calendar invite, schedule, interviewer
  names, candidate name/email.
- **`event`** — discrete state changes: join/leave, `webcam_on`/`off`,
  `speaking_start`/`end`, `transcript_segment`, etc. Sparse, low
  frequency. `webcam_on`/`audio_stream_on`/`screenshare_start` carry
  **track metadata only** (`width`/`height`/`fps` for video,
  `sample_rate`/`encoding`/`channels` for audio) — **never a file
  path.** A real Meet/Zoom/Teams adapter can't hand you a path into its
  own filesystem; it hands you a live track. Shipping a path here would
  mean building Engine identifiers against a data contract ("read this
  file whenever you like") that doesn't exist in production, so we
  don't.
- **`stream`** — the actual media payload for a currently-open track:
  base64-encoded bytes, tagged `participant_id` + `modality`
  (`"audio"|"video"|"screenshare"`) + `seq`, paced in real time between
  the matching `_on`/`_off` events (scaled by `speed_multiplier`, same
  clock as everything else). Chunk rate is a stated, tunable assumption
  (`VIDEO_CHUNK_FPS`, `AUDIO_CHUNK_MS` in `compiler.py`), not a claim
  about a source's "native" rate — our sources (looped stills, TTS
  wavs) don't have one. A real adapter would chunk at the actual camera
  fps / RTP cadence instead.

Both event and stream timestamps are computed once, up front, at
compile time and merged into one flat sorted list — `emitter.py` stays
a single sequential walker (sleep-to-next-`t`, yield, repeat), the same
as before streaming existed. No concurrency was needed to add this.

## Pipeline
`index.yml` → validate (`validator.py`) → compile (`compiler.py`, cached
under `scenario_dir/.cache/` — this now also decodes each media window
into stream-chunk boundaries once, per window, not per chunk) → emit
(`emitter.py`, which lazily reads+base64-encodes each chunk's bytes at
emit time).

## Schema (`index.yml`)
```yaml
metadata:      # name, slug, description (pure identity + human framing)
controls:      # speed_multiplier, generate_audio (bool) - runtime knobs only
context:       # calendar invite, schedule, interviewer names, candidate name/email
participants:  # id -> display_name, role_hint (pure identity, no media here)
timeline:      # list of events, in the order they happen - see below
evaluation:    # ground_truth_participant_id, difficulty (1-5), challenging_points,
               # expected_evidence {primary, secondary, misleading} - grading/
               # dashboard-only, NEVER sent down emit()'s wire stream
```

### Timeline: no `t` field, ever
Position is pure chronological **list order**. Only two event types
advance the compiler's clock:
- `silence: {duration}` — explicit gap.
- `audio_stream_on: {text}` or `{path}` — clock advances by the real
  measured/generated clip duration (never an author guess), and the
  matching `audio_stream_off` is auto-inserted by the compiler. Do not
  hand-author `audio_stream_off` — it's rejected by validation.

Everything else (`participant_join/leave`, `webcam_on/off`,
`screenshare_start/end`, `speaking_start/end`, `transcript_segment`) is
stamped at whatever the clock currently reads — it does not advance time.

### Media lives on events, not participants
- `webcam_on: {path}` — path can be a video **or a static image**. The
  compiler loops (if shorter) or trims (if longer) the source to exactly
  fill the window up to the matching `webcam_off`.
- `audio_stream_on: {text}` — TTS-generated (offline, via espeak-ng),
  with a distinct, deterministic voice per participant. Set
  `audio_stream_on: {path}` instead to use a real audio file — explicit
  media always wins over generation for that event.
- `controls.generate_audio: false` disables TTS globally; any
  `audio_stream_on` using only `text` (no `path`) becomes a validation error.
- Authoring format is unchanged by the streaming rework above — you
  still just write `webcam_on: {path: ...}` / `audio_stream_on: {text:
  ...}`. What changed is what the compiler resolves it *into*: instead
  of one path attached to the on-event, it's track metadata on the
  event plus a run of `stream` chunks between on and off.

### Caching
Compiled output (resolved timeline + all generated/synthesized media
paths) is cached at `scenario_dir/.cache/compiled.json`, invalidated by
a content hash of `index.yml`. Edit the source, next compile detects the
mismatch and redoes only the necessary work. `.cache/` is gitignored —
never commit it.

## Design notes
- One clock only: the event-scheduling clock (`speed_multiplier` scales
  playback in `emitter.py`). The simulator never has a global "fps" —
  video/audio sampling rate is entirely the Engine's decision downstream.
- Validation is common-sense level: required fields present, participant_id
  references resolve, media paths that are given resolve to real files,
  webcam on/off pairing is well-formed. Optional fields may be null.
