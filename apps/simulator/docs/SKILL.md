---
name: scenario-authoring
description: Use this skill whenever creating, editing, or reviewing an index.yml scenario file for the Sherlock candidate-identification simulator (apps/simulator). Trigger on any mention of "scenario", "index.yml", "test case for the simulator", or requests to simulate a specific interview edge case (nickname mismatch, wrong name, multiple interviewers, silent observers, rename, missing info, etc). Do not use for engine/dashboard code — this covers simulator input authoring only.
---

# Scenario Authoring (Sherlock Simulator)

## What this is

A scenario is one directory: `scenario_dir/index.yml` (+ optional
`scenario_dir/media/*` source files you reference by relative path).
The simulator validates it, compiles it once (cached under
`scenario_dir/.cache/`, never hand-edit or commit that directory —
it's gitignored and invalidated automatically by a content hash of
`index.yml`), and replays it as a timed event stream that should be
indistinguishable, on the wire, from a real Meet/Zoom/Teams adapter.

Ground truth for **who the candidate actually is** lives in
`evaluation.ground_truth_participant_id` — it is never sent down the
event stream itself, only used for offline scoring/evaluation. Every
other identity signal an Engine would use has to be inferred from the
event stream + context, same as production.

## Hard requirements (validator.py — self-check before running `validate`)

Top-level: `metadata`, `context`, `participants`, `timeline` are all
**required** keys, checked before anything else runs. `controls` and
`evaluation` are optional top-level sections (both have all-optional
or defaulted fields).

**`metadata`** — pure identity/framing, nothing gradable, no runtime knobs.
- `name`, `slug`, `description` — required, non-empty. `description` is
  human-facing (dashboard) prose: state plainly what this scenario is
  and why it exists. This replaced the old `remarks` field 1:1 — same
  purpose, same content, just required now instead of optional.

**`controls`** — optional section, runtime/playback knobs only.
- `speed_multiplier` — optional float, default `1.0`. Scales wall-clock
  playback speed only (e.g. `20.0` for a fast demo run) — never
  changes relative event ordering or durations.
- `generate_audio` — optional bool, default `true`. If `false`, every
  `audio_stream_on` in the timeline **must** supply `data.path` (an
  audio-only `text` becomes a validation error).

**`evaluation`** — optional section, grading/dashboard-only, **never
sent down `emit()`'s event stream** — only reachable via the
`/evaluation` HTTP endpoint or `scenario.evaluation` in code, both
author/scoring tooling, never anything an Engine talks to.
- `ground_truth_participant_id` — optional, but if present **must** be
  a key in `participants`.
- `difficulty` — optional integer, `1` (easiest) to `5` (hardest).
- `challenging_points` — optional list of strings: the specific
  obstacles an identification system has to overcome in this scenario.
- `expected_evidence` — optional map. Allowed keys exactly `primary`,
  `secondary`, `misleading` (unrecognized keys are a validation error);
  each value is a list of free-text strings. The simulator does not
  interpret these strings — rendering/scoring against an Engine's
  actual output is entirely the dashboard's responsibility.

**`context`**
- `candidate_name`, `candidate_email` — the only two **required**
  fields.
- `calendar_invite`, `interview_schedule`, `interviewer_names` — all
  optional, default to `{}`/`{}`/`[]` if omitted. `interviewer_names`
  is a list of plain name strings — external metadata, **not**
  participant IDs, and nothing cross-checks it against who actually
  joins. Use a mismatch here deliberately to simulate bad scheduling
  data.

**`participants`** (dict: `participant_id` → record)
- `display_name` — required, non-empty.
- `role_hint` — optional, free string (`"candidate"` / `"interviewer"`
  / `"observer"` / `null` are the convention but **nothing validates
  this** — typos pass silently). It is decorative for the simulator:
  no validator/compiler logic reads it. Only
  `evaluation.ground_truth_participant_id` is scored truth.
- This dict is the compiler's identity registry, but **it is never
  itself transmitted on the wire.** `emit()` only sends `context` once
  and then timeline events — never a participant roster dump. See
  below.

**`timeline`** — ordered list, **no `t` field ever**. Position in the
list = chronological order. Only two event types advance the
compiler's clock:
- `silence: {duration: <seconds>}`
- `audio_stream_on` — clock advances by the *real measured/generated*
  clip duration (TTS via offline espeak-ng/pyttsx3, or `ffprobe` of an
  explicit `path`) — never an author guess.

Every other event type is stamped at whatever the clock currently
reads and does **not** advance it: `participant_join`,
`participant_leave`, `participant_update`, `webcam_on`, `webcam_off`,
`screenshare_start`, `screenshare_end`, `speaking_start`,
`speaking_end`, `transcript_segment`.

### Authorable event types and their exact rules

| Event | Required data | Validator enforces |
|---|---|---|
| `participant_join` | none, but **put `data.display_name` on it** | `participant_id` known. **Nothing auto-fills this from the `participants` dict** — if you omit `data.display_name`, the consumer gets a bare ID with no name, silently. Always author it explicitly. |
| `participant_leave` | none | `participant_id` known |
| `participant_update` | at least one of `data.display_name` / `data.role_hint`, and `display_name` if given must be non-empty | This is the **mid-session identity-change event** — e.g. a rename, or a corrected role. Use this, not a second `participant_join`, to represent "candidate changes their display name." A second `participant_join` for the same ID is undefined/ambiguous (looks like a rejoin) — don't use it for renames. |
| `webcam_on` | `data.path` (image or video), must resolve to a real file | Pairing enforced: no double-`on` without an intervening `off`; no dangling `on` left open at end of timeline. Compiler loop-fits (loops if shorter, trims if longer, same ffmpeg mechanism for both) the source to exactly the `on..off` window once `webcam_off` is reached. |
| `webcam_off` | none | Must have a matching open `webcam_on` |
| `screenshare_start` | `data.path` **optional** | Pairing enforced identically to webcam (no double-start, no dangling start, no orphan end). If `data.path` given: validated + loop-fit exactly like webcam media. If omitted: valid marker-only event (share happened, no recorded content modeled). |
| `screenshare_end` | none | Must have a matching open `screenshare_start` |
| `speaking_start` / `speaking_end` | none | `participant_id` known only — **no pairing enforcement** (unlike webcam/screenshare). Author carefully. |
| `transcript_segment` | freeform `data` (e.g. `{text: ...}`) | `participant_id` known only, no content validation |
| `audio_stream_on` | `data.path` **or** `data.text` (at least one) | If `path` given, resolved + file-existence checked (explicit media always wins over generation). If only `text`, requires `controls.generate_audio: true`. |
| ~~`audio_stream_off`~~ | — | **Never hand-author this.** Validator rejects it outright — the compiler auto-derives and inserts it right after measuring real audio duration. |
| `silence` | numeric `data.duration` | clock-advancing, never emitted downstream |

### Speaking duration vs. audio — do not conflate

`speaking_start`/`speaking_end` is the canonical signal for how long
someone spoke. It is **independent** of `audio_stream_on`/`off` (which
only controls what audio media file plays). The correct, most
information-rich pattern is to bracket the audio:

```yaml
- type: speaking_start
  participant_id: p_candidate
- type: audio_stream_on
  participant_id: p_candidate
  data: {text: "..."}
- type: speaking_end
  participant_id: p_candidate
```

Since `speaking_start`/`speaking_end` don't advance the clock
themselves, placing `speaking_end` *after* the audio pair in list
order correctly captures the true speaking window regardless of the
underlying audio file's exact duration. Do not rely on
`audio_stream_on`/`off` timing alone as a stand-in for speaking
duration — it happens to work when they're 1:1, but it's the wrong
signal to read for that purpose and breaks the moment you want speech
without synthesized audio, or audio without a clean speaking window.

### Media path resolution

Identical rule for `webcam_on.data.path`, `screenshare_start.data.path`,
and `audio_stream_on.data.path`: absolute paths are used as-is;
relative paths resolve against `scenario_dir` (`os.path.join` +
`normpath`). Convention: put source media under `scenario_dir/media/`.

### What actually gets sent on the wire

`emit()` yields exactly two message kinds, in this order:
1. `("context", SessionContext)` — once, first. Contains only
   `calendar_invite`, `interview_schedule`, `interviewer_names`,
   `candidate_name`, `candidate_email`.
2. `("event", Event)` — once per compiled timeline entry, each with
   `t`, `type`, `participant_id`, `data`.

There is **no separate "participant roster" message** — an Engine
only learns about a participant by seeing their `participant_join`
event (and only learns their name if that event's `data.display_name`
was authored).

### Running / validating

```
uv run src/cli.py validate scenarios/<slug>
uv run src/cli.py run scenarios/<slug>          # console dry-run
uv run src/cli.py serve scenarios/<slug>        # ws://0.0.0.0:8765 — the real interface
```
Wire format on `serve`: newline-free JSON per message,
`{"kind": "context"|"event"|"error", "payload": {...}}`. Each
connecting client gets a fresh replay from `t=0`. There is also an
HTTP/SSE-based `api.py` (`/validate`, `/run`, `/evaluation`) — a
different wire protocol from `serve`'s raw websocket; `serve`/`/run`
are what a real Engine should connect to. `/validate` and
`/evaluation` are author/scoring tooling only; `/evaluation` is the
one endpoint that returns `ground_truth_participant_id` and the rest
of the `evaluation` section — never point an Engine at it.

## Common validation-error messages (self-correct against these)

- `metadata.name is required` / `metadata.slug is required` / `metadata.description is required`
- `evaluation.ground_truth_participant_id '<id>' is not a declared participant`
- `evaluation.difficulty must be an integer 1 (easiest) - 5 (hardest)`
- `evaluation.expected_evidence has unrecognized key(s) [...] - allowed: ['misleading', 'primary', 'secondary']`
- `context.candidate_name is required` / `context.candidate_email is required`
- `participants: at least one participant is required`
- `participants.<id>.display_name is required`
- `timeline[<i>].type is required`
- `timeline[<i>].type '<x>' is not a recognized event type`
- `timeline[<i>]: 'audio_stream_off' must not be hand-authored — the compiler derives it automatically`
- `timeline[<i>]: '<type>' requires participant_id`
- `timeline[<i>].participant_id '<id>' is not declared in participants`
- `timeline[<i>]: webcam_on for '<id>' but webcam is already on (missing webcam_off before this)`
- `timeline[<i>]: webcam_off for '<id>' but webcam was not on`
- `timeline: webcam for '<id>' was turned on but never turned off`
- `timeline[<i>]: screenshare_start for '<id>' but a screenshare is already open (missing screenshare_end before this)`
- `timeline[<i>]: screenshare_end for '<id>' but no screenshare was open`
- `timeline: screenshare for '<id>' was started but never ended`
- `timeline[<i>]: participant_update requires at least one of data.display_name or data.role_hint`
- `timeline[<i>]: data.path does not resolve to a file: '<path>' -> '<resolved>'`
- `timeline[<i>]: audio_stream_on requires data.path or data.text`
- `timeline[<i>]: audio_stream_on has only 'text' (no data.path) but controls.generate_audio is false`
- `timeline: silence requires numeric data.duration`

## Scenario design checklist (map challenge edge cases → recipes)

| Challenge scenario | How to author it |
|---|---|
| Candidate joins as device name | `participants.<id>.display_name: "MacBook Pro"`, real name only in `context.candidate_name` |
| Candidate joins with a nickname | Same pattern — display name that doesn't string-match `context.candidate_name` |
| Interviewer enters the wrong candidate name | Make `context.candidate_name` mismatch the actual candidate participant's real identity (revealed only via transcript/behavior, not name) |
| Multiple interviewers present | 2+ participants with `role_hint: interviewer`, all listed in `context.interviewer_names` |
| Candidate changes display name mid-call | `participant_update` event, same `participant_id`, later in the timeline (see rules above) |
| Multiple silent observers | `role_hint: observer` (decorative only), no `audio_stream_on`/`speaking_start` events for them ever, webcam off or a static image |
| Missing external metadata | Omit `calendar_invite`/`interview_schedule`/`interviewer_names` entirely (all optional) |
| Candidate never enables webcam | Simply author no `webcam_on`/`webcam_off` pair for that participant — not required per-participant |
| Screen-shared coding round | `screenshare_start` with `data.path` on the candidate for a loop-fit recording, or path-less for a bare marker |
| No ground truth (edge case) | Omit `evaluation.ground_truth_participant_id` and say so explicitly in `metadata.description` — don't leave it silently ambiguous |

Always write `metadata.description` (required) to state, in plain
language, exactly which edge-case behavior the scenario is designed to
stress. Use `evaluation.challenging_points` to break that same intent
down into a checklist of specific obstacles, and
`evaluation.expected_evidence` to record which signals should point
where — description is prose for a human skimming the scenario;
challenging_points/expected_evidence are the same intent made
structured enough for a dashboard or scoring pass to use.

## Known gaps (don't silently paper over these)

- `speaking_start`/`speaking_end` and `screenshare_start`/`data.path`-less
  markers have **no cross-participant overlap protection** —
  authoring is on you.
- `audio_stream_on` is strictly serialized on the clock — two
  participants can never have overlapping speech in this model. No
  interruptions/cross-talk are representable today.
- The compiled `Participant` registry (`scenario.participants`) is
  fixed at compile time from the top-level `participants` dict —
  `participant_update` events do **not** retroactively update it. The
  CLI's console dry-run (`describe_event`) will keep showing the
  *original* display name even after a `participant_update` — this is
  cosmetic-only and does not affect what's sent on the wire (the
  Engine sees the update event itself, with the new name, in real
  time).
