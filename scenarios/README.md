# Scenario set for the Sherlock candidate-identification simulator

10 scenarios, each a self-contained `index.yml` + `media/` folder. Drop
this whole `scenarios/` directory in as your simulator's `scenarios/`
(or point the CLI at each subfolder directly). All ten pass
`uv run python -m simulator validate scenarios/<slug>`.

| Slug | Difficulty | Edge case stressed |
|---|---|---|
| `demo_clean` | 3 | Baseline: nickname/device-name mismatch, two interviewers, one decoy silent observer with an equally device-like name. (Reference scenario, included as-is.) |
| `wrong_scheduled_name` | 4 | HR's `candidate_name` matches *nobody* on the call, in any field, ever — identity has to come from pure behavior with zero usable name signal. |
| `multi_interviewer_decoy` | 3 | Two interviewers, both listed in `interviewer_names`; one of them briefly screenshares too, so "who shared their screen" alone isn't a safe candidate signal. |
| `silent_observers_farm` | 2 | Candidate's name matches cleanly (isolating the noise-rejection problem); three silent observers with very different name flavors (device, room, "Unknown") must all rank low uniformly, including one that turns its webcam on. |
| `mid_call_rename` | 2 | Candidate joins as "Guest", is asked to confirm their name, self-identifies verbally, *then* issues a `participant_update` — tests picking up the transcript-level self-ID before the formal rename event lands. |
| `missing_metadata` | 3 | `calendar_invite`, `interview_schedule`, and `interviewer_names` are all omitted — only the two mandatory `context` fields exist. Also has a partial (not exact) name match. |
| `screenshare_coding_round` | 1 | Identity is easy (clean name match) on purpose — this one stresses sustained *concurrent* webcam + screenshare + audio + transcript activity and `track_id` correlation, not identity reasoning. |
| `video_degraded_audio_clear` | 2 | `controls.video_fps: 0.5` / `audio_chunk_ms: 100` — tests that video-dependent signals degrade gracefully without dragging down confidence from still-solid audio/transcript evidence. Independent-knob test, not an identity puzzle. |
| `webcam_never_on` | 2 | Candidate has *no* `webcam_on`/`webcam_off` pair at all (not "off", just never present) — identification must work from audio/transcript/turn-taking alone; absence of video should not be treated as suspicious or as an error state. |

## How these were authored

Built against `docs/SCENARIO_AUTHORING.md` (the human-readable "why")
and `docs/SKILL.md` (the strict field-by-field reference) from the
codebase. Every scenario follows the same shape:

- `metadata` — plain-language description of exactly which edge case
  is being stressed and why.
- `controls` — `speed_multiplier: 20.0` on all of them so replay is
  fast for demoing; a couple deliberately override `video_fps` /
  `audio_chunk_ms` to test independent stream granularity.
- `context` — `candidate_name`/`candidate_email` always present;
  `calendar_invite`/`interview_schedule`/`interviewer_names` varied
  (including fully omitted in `missing_metadata`) to test graceful
  degradation.
- `participants` / `timeline` — hand-authored dialogue via
  `audio_stream_on.data.text` (TTS-generated, `generate_audio: true`
  everywhere), each `speaking_start`/`audio_stream_on`/
  `transcript_segment`/`speaking_end` bracketed per the SKILL's
  recommended pattern.
- `evaluation` — `ground_truth_participant_id` (omitted, on purpose,
  only in `ambiguous_no_ground_truth`), `difficulty` 1–5,
  `challenging_points`, and `expected_evidence.{primary,secondary,misleading}`
  spelling out exactly what should and shouldn't move the needle for
  a scoring pass.

## Validating

```bash
cd <your simulator repo>
for d in /path/to/this/scenarios/*/; do
  uv run python -m simulator validate "$d"
done
```

All ten were validated (locally, against a standalone copy of
`validator.py`'s logic — the full `simulator` package wasn't
installable in the authoring sandbox) before packaging. Re-run
`validate` yourself after dropping them into your repo as a sanity
check, especially if you tweak `controls.generate_audio` to `false`
anywhere (all ten currently rely on TTS `text`, not `path`, for
audio).
