# Scenario Simulator

Emits meeting events (join/leave, webcam, screenshare, speaking, transcript,
media streams) from a hand-authored `index.yml`, timed and sequenced exactly
like a real Meet/Zoom/Teams adapter would deliver them to the Engine.

## Usage
    uv run src/cli.py validate scenarios/demo_clean
    uv run src/cli.py run scenarios/demo_clean

## Pipeline
index.yml -> validate (validator.py) -> compile (compiler.py) -> emit (emitter.py)

## Design notes
- One clock only: event-scheduling time (`t` in seconds, scaled by
  `metadata.speed_multiplier`). No separate "fps" for the simulator itself.
- `media_stream_start` hands the consumer a file path + kind, it does not
  decode frames. Video/audio sampling rate is the Engine's decision, not
  the simulator's.
- If a participant has only `video_path`, audio is auto-extracted via
  ffmpeg at compile time and cached as `<name>.extracted.wav`.
- Media paths may be relative (resolved against the scenario dir) or absolute.
- Validation is "common sense" level: required fields present, participant_id
  references resolve, media paths resolve. Optional fields may be null.
