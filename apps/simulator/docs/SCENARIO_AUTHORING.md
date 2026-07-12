# Writing Scenarios for the Simulator

This is the human-readable version of the scenario format. If you're
an AI agent authoring `index.yml` files, use
`.claude/skills/scenario-authoring/SKILL.md` instead — it's the
strict, exhaustive reference. This doc is the "why" and the "what
we're actually trying to test."

## Why scenarios exist

The Engine (candidate identification pipeline) can't be built or
evaluated against real interviews — no ground truth, no repeatability,
privacy concerns. Instead, we hand-author `index.yml` files that
describe a fake interview session end-to-end: who joins, when, what
their camera/mic/screen do, what gets said, and — critically — who the
candidate *actually* is. The simulator turns that into a realistic
timed event stream over a websocket, so the Engine can be built and
tested against it exactly as if it were a real Meet/Zoom/Teams call,
with a known right answer to score against afterwards.

The single most important design idea: **the "right answer"
(`ground_truth_participant_id`) is never sent down the event stream.**
It only exists for scoring, on a side channel the Engine never sees.
Everything the Engine gets to work with — display names, join order,
who speaks when, what's said, webcam/screenshare activity — is exactly
what a real adapter would give it. If a scenario's answer is
"obvious" from the raw events, that's not a useful scenario; you've
either made a happy-path case (fine, you need a few of these as a
baseline) or accidentally leaked the answer somewhere it shouldn't be.

The same "exactly what a real adapter would give it" principle applies
to *how* media is delivered, not just what facts are in the events: a
real Meet/Zoom/Teams adapter never hands you a file path to open at
your leisure, it pushes you a live track. So webcam/audio/screenshare
in `index.yml` are still authored as a path or TTS text (see below) —
that part hasn't changed — but on the wire, the on-event only carries
track metadata (resolution/fps, or sample rate/encoding) and the
actual bytes arrive afterward as a separate, real-time-paced stream of
chunks. If you're building the Engine side against this simulator,
don't read `data.path` off a `webcam_on`/`audio_stream_on` event —
there isn't one; consume the `stream` messages instead.

## The basic shape

A scenario is a folder:

```
scenarios-ref/demo_clean/ # normal ones goes in scenarios/ [and it's ignored by git] (this is just reference)
  index.yml          # the scenario itself
  media/              # your source images/videos/audio, referenced by relative path
    candidate.mp4
    observer.png
  .cache/              # generated automatically, gitignored — never touch or commit
```

`index.yml` has six sections:

- **`metadata`** — `name`, `slug`, and `description` (required,
  human-readable — state in plain language exactly what this scenario
  is and, at a glance, why it exists). Pure identity/framing only —
  nothing gradable and no runtime knobs live here.
- **`controls`** — runtime/playback knobs, not scenario content:
  `speed_multiplier` (crank this up, e.g. `20.0`, so demo runs don't
  take real interview-length time) and `generate_audio` (turn off if
  you're supplying real audio files instead of letting the simulator
  TTS the lines). Both optional, both default sensibly.
- **`context`** — the "external metadata" a real system would already
  have before the meeting even starts: calendar invite, scheduled
  time, interviewer names, and the candidate's name/email as HR
  believes them to be. Only `candidate_name` and `candidate_email` are
  mandatory; everything else can be left out to simulate incomplete
  scheduling data.
- **`participants`** — the cast list. Each entry is just an ID you
  invent, a display name, and an optional (currently unenforced, purely
  for your own bookkeeping) role hint.
- **`timeline`** — the actual events, in the order they happen. There's
  no explicit timestamp field — position in the list *is* the order,
  and the simulator's compiler works out real timestamps for you based
  on how long things actually take (audio duration, explicit pauses).
- **`evaluation`** — grading/dashboard-only metadata, optional as a
  whole section: `ground_truth_participant_id`, `difficulty` (integer
  `1`–`5`, `1` easiest), `challenging_points` (list of strings — the
  specific obstacles an identification system has to get past),
  `expected_evidence` (a map of `primary`/`secondary`/`misleading` to
  lists of free-text strings describing what evidence points where —
  it's the dashboard's job to decide how to render or score these
  against an Engine's actual output, the simulator just carries them).
  **This entire section is never sent down `emit()`'s event stream** —
  it's only reachable via the `/evaluation` HTTP endpoint or the
  compiled `scenario.evaluation` object in a console/CLI context, both
  of which are author/scoring tooling, not anything an Engine talks to.

## What matters when you're designing a scenario

The whole point of this system is that **name-matching alone is not
enough** — that's the premise of the challenge. So when you write a
scenario, always ask: *what happens if the Engine only had the
candidate's name to go on?* If the answer is "it would get this
wrong," you've written a useful scenario. Some recipes:

- **Nickname / device-name mismatch** — candidate's `display_name` is
  `"MacBook Pro"` or `"Suraj"` while `context.candidate_name` says
  `"Suraj Thapa"`. Already in `demo_clean`.
- **Wrong name in the scheduling system** — this is subtler: make
  `context.candidate_name` refer to someone who isn't accurately
  represented by any participant's display name at all. The only way
  to find the real candidate is behavioral (who's answering interview
  questions, who joined at the scheduled time, etc.) — not string
  matching.
- **Multiple interviewers** — list two or more interviewer
  participants, and make sure `context.interviewer_names` matches so a
  system that trusts that list has something to exclude against.
- **Silent observers** — participants who join, maybe show a static
  photo, and never speak or share anything. These should sit near the
  bottom of any confidence ranking without extra special-casing them.
- **Mid-call rename** — a participant corrects their display name
  partway through (e.g. joined as "iPhone", then updates to their real
  name once asked). Use a `participant_update` event for this, not a
  second join — see the SKILL for the exact shape.
- **Missing information** — just omit optional context fields
  (`calendar_invite`, `interview_schedule`) to test how gracefully the
  system degrades without them. It should not silently assume more
  confidence than the evidence supports.

## Assumptions and limitations, stated plainly

- **One speaker at a time.** The simulator's clock is fully serial —
  it cannot represent two people talking over each other. If your
  identification approach depends on interruption patterns, you can't
  test that here yet.
- **`role_hint` is not ground truth and isn't checked by anything.**
  It's a note to yourself as a scenario author. Don't build engine
  logic that assumes it's reliable or even present — real systems
  won't have it either.
- **Screen-share and speaking-activity pairing is on you.** Unlike
  webcam, some of these events technically don't get overlap
  protection in every case — double-check your own timelines.
- **A participant's display name, once set at join time, is what the
  Engine has to work with until a `participant_update` says
  otherwise.** There's no roster snapshot resent periodically — if you
  don't put `display_name` on the `participant_join` event itself, the
  Engine genuinely never learns it (not a simulator bug — this is
  deliberately how a real adapter behaves too).

## Running what you wrote

```bash
uv run src/cli.py validate scenarios/demo_clean   # catch mistakes before anything else
uv run src/cli.py run scenarios/demo_clean        # console dry-run, human-readable
uv run src/cli.py serve scenarios/demo_clean      # the real thing: opens a websocket
```

`validate` will tell you exactly what's wrong (missing fields, dangling
webcam/screenshare, bad file paths) with a line-numbered message — fix
those before worrying about anything downstream. `run` is for
sanity-checking timing and content by eye. `serve` is what the Engine
actually connects to during development and demoing.
