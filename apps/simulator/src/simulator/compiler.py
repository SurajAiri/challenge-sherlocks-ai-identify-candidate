"""
Compile: validated raw dict -> CompiledScenario, cached as a .compiled
artifact under scenario_dir/.cache/ so repeat runs don't redo TTS
generation or ffmpeg synthesis. Invalidated by a content hash of
index.yml - edit the source, next compile detects the mismatch and
redoes the work.

Timeline resolution is a single forward pass over the authored event
list, in list order:
  - silence: advances the clock, emits nothing.
  - webcam_on / webcam_off: NOT clock-advancing. Stamped at whatever
    `current_t` is when they're encountered in list order. Once the
    matching webcam_off is seen, the on..off duration is known, and
    the source image/video is synthesized (looped or trimmed) to fill
    exactly that window.
  - audio_stream_on: clock-advancing. Duration comes from the real
    measured/generated clip (TTS from `text`, or ffprobe of an explicit
    `path`) - never an author guess, so there's nothing to reconcile
    after the fact. `audio_stream_off` is auto-inserted right after.
  - everything else (join/leave/screenshare/speaking/transcript): not
    clock-advancing, stamped at `current_t`.

Because `Event.t` is filled in when each event is resolved (not
necessarily in final chronological order - webcam_on's final record is
only completed once webcam_off is reached), the full list is sorted by
t once at the end.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Optional

import yaml

from simulator.media_gen import (
    extract_audio_pcm,
    extract_video_frames,
    ffprobe_duration,
    ffprobe_video_size,
    synthesize_tts,
    synthesize_webcam_clip,
)
from simulator.models import (
    CompiledScenario,
    Event,
    EventType,
    Participant,
    ScenarioControls,
    ScenarioEvaluation,
    ScenarioMetadata,
    SessionContext,
    StreamChunk,
)
from simulator.validator import ValidationError, resolve_media_path, validate

CACHE_DIRNAME = ".cache"
COMPILED_FILENAME = "compiled.json"

# Chunking rates. These are a stated, tunable assumption - NOT a claim
# about a "real" native rate, since our sources (looped stills, TTS
# wavs) don't have a meaningful native one. A real adapter would chunk
# at the source's actual camera fps / RTP packet cadence instead; pick
# these to reflect whatever your Engine's identifiers actually need to
# sample, not for their own sake.
VIDEO_CHUNK_FPS = 5.0
AUDIO_CHUNK_MS = 200
AUDIO_SAMPLE_RATE = 16000


def load_yaml(index_path: str) -> dict:
    with open(index_path, "r") as f:
        return yaml.safe_load(f)


def _source_hash(index_path: str) -> str:
    with open(index_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _to_jsonable(scenario: CompiledScenario, source_hash: str) -> dict:
    return {
        "source_hash": source_hash,
        "metadata": vars(scenario.metadata),
        "controls": vars(scenario.controls),
        "evaluation": vars(scenario.evaluation),
        "context": vars(scenario.context),
        "participants": {pid: vars(p) for pid, p in scenario.participants.items()},
        "timeline": [
            {
                "kind": "event",
                "t": e.t,
                "type": e.type.value,
                "participant_id": e.participant_id,
                "data": e.data,
            }
            if isinstance(e, Event)
            else {
                "kind": "chunk",
                "t": e.t,
                "participant_id": e.participant_id,
                "modality": e.modality,
                "seq": e.seq,
                "source_path": e.source_path,
                "byte_offset": e.byte_offset,
                "byte_length": e.byte_length,
            }
            for e in scenario.timeline
        ],
        "scenario_dir": scenario.scenario_dir,
    }


def _from_jsonable(d: dict) -> CompiledScenario:
    return CompiledScenario(
        metadata=ScenarioMetadata(**d["metadata"]),
        controls=ScenarioControls(**d["controls"]),
        evaluation=ScenarioEvaluation(**d["evaluation"]),
        context=SessionContext(**d["context"]),
        participants={pid: Participant(**p) for pid, p in d["participants"].items()},
        timeline=[
            Event(
                t=e["t"],
                type=EventType(e["type"]),
                participant_id=e["participant_id"],
                data=e["data"],
            )
            if e["kind"] == "event"
            else StreamChunk(
                t=e["t"],
                participant_id=e["participant_id"],
                modality=e["modality"],
                seq=e["seq"],
                source_path=e["source_path"],
                byte_offset=e["byte_offset"],
                byte_length=e["byte_length"],
            )
            for e in d["timeline"]
        ],
        scenario_dir=d["scenario_dir"],
    )


def _video_chunks(
    clip_path: str, t_on: float, media_cache_dir: str, pid: str, modality: str
) -> list[StreamChunk]:
    """Expand one webcam/screenshare on..off window into per-frame
    StreamChunks. Frames are decoded ONCE (extract_video_frames caches
    the whole batch) - this just assigns timestamps/seq, no ffmpeg call
    per chunk."""
    frames = extract_video_frames(clip_path, VIDEO_CHUNK_FPS, media_cache_dir)
    return [
        StreamChunk(
            t=t_on + i / VIDEO_CHUNK_FPS,
            participant_id=pid,
            modality=modality,
            seq=i,
            source_path=frame_path,
        )
        for i, frame_path in enumerate(frames)
    ]


def _audio_chunks(pcm_path: str, t_on: float, pid: str) -> list[StreamChunk]:
    """Expand one audio_stream_on..off window into fixed-size raw-PCM
    byte-range StreamChunks, all pointing at the SAME already-decoded
    flat pcm_path (extract_audio_pcm decodes once) - just offset/length
    bookkeeping here, no re-decoding per chunk."""
    bytes_per_sample = 2  # s16le mono
    chunk_bytes = int(AUDIO_SAMPLE_RATE * (AUDIO_CHUNK_MS / 1000) * bytes_per_sample)
    total_bytes = os.path.getsize(pcm_path)

    chunks = []
    offset = 0
    i = 0
    while offset < total_bytes:
        length = min(chunk_bytes, total_bytes - offset)
        chunks.append(
            StreamChunk(
                t=t_on + i * (AUDIO_CHUNK_MS / 1000),
                participant_id=pid,
                modality="audio",
                seq=i,
                source_path=pcm_path,
                byte_offset=offset,
                byte_length=length,
            )
        )
        offset += length
        i += 1
    return chunks


def _compile_fresh(
    raw: dict, scenario_dir: str, driverName: str | None = None
) -> CompiledScenario:
    md = raw["metadata"]
    metadata = ScenarioMetadata(
        name=md["name"],
        slug=md["slug"],
        description=md.get("description"),
    )

    ctl = raw.get("controls") or {}
    controls = ScenarioControls(
        speed_multiplier=float(ctl.get("speed_multiplier", 1.0)),
        generate_audio=bool(ctl.get("generate_audio", True)),
    )

    ev = raw.get("evaluation") or {}
    evaluation = ScenarioEvaluation(
        ground_truth_participant_id=ev.get("ground_truth_participant_id"),
        difficulty=ev.get("difficulty"),
        challenging_points=list(ev.get("challenging_points") or []),
        expected_evidence=dict(ev.get("expected_evidence") or {}),
    )

    ctx = raw["context"]
    context = SessionContext(
        calendar_invite=ctx.get("calendar_invite", {}),
        interview_schedule=ctx.get("interview_schedule", {}),
        interviewer_names=ctx.get("interviewer_names", []),
        candidate_name=ctx["candidate_name"],
        candidate_email=ctx["candidate_email"],
    )

    participants = {
        pid: Participant(
            participant_id=pid,
            display_name=pdata["display_name"],
            role_hint=(pdata or {}).get("role_hint"),
        )
        for pid, pdata in raw["participants"].items()
    }

    media_cache_dir = os.path.join(scenario_dir, CACHE_DIRNAME, "media")

    current_t = 0.0
    output: list[Event] = []
    pending_webcam: dict[
        str, tuple[float, str]
    ] = {}  # pid -> (t_on, resolved_src_path)
    pending_screenshare: dict[
        str, tuple[float, Optional[str]]
    ] = {}  # pid -> (t_on, resolved_src_path or None if marker-only)

    for ev in raw["timeline"]:
        ev = ev or {}
        ev_type = ev["type"]
        pid = ev.get("participant_id")
        data = dict(ev.get("data") or {})

        if ev_type == "silence":
            current_t += float(data["duration"])
            continue

        if ev_type == "webcam_on":
            resolved_src = resolve_media_path(data["path"], scenario_dir)
            pending_webcam[pid] = (current_t, resolved_src)
            continue

        if ev_type == "webcam_off":
            t_on, src_path = pending_webcam.pop(pid)
            duration = current_t - t_on
            clip_path = synthesize_webcam_clip(
                src_path, max(duration, 0.1), media_cache_dir
            )
            width, height = ffprobe_video_size(clip_path)
            output.append(
                Event(
                    t=t_on,
                    type=EventType.WEBCAM_ON,
                    participant_id=pid,
                    # marker + track metadata only - no path. Actual
                    # frames arrive as "stream" chunks between this and
                    # the matching webcam_off, same as a real adapter.
                    data={"width": width, "height": height, "fps": VIDEO_CHUNK_FPS},
                )
            )
            output.extend(_video_chunks(clip_path, t_on, media_cache_dir, pid, "video"))
            output.append(
                Event(
                    t=current_t, type=EventType.WEBCAM_OFF, participant_id=pid, data={}
                )
            )
            continue

        if ev_type == "screenshare_start":
            path = data.get("path")
            resolved_src = resolve_media_path(path, scenario_dir) if path else None
            pending_screenshare[pid] = (current_t, resolved_src)
            continue

        if ev_type == "screenshare_end":
            t_on, src_path = pending_screenshare.pop(pid)
            if src_path is None:
                # marker-only screenshare (no recorded content) - pass both
                # ends through unchanged, no media synthesis.
                output.append(
                    Event(
                        t=t_on,
                        type=EventType.SCREENSHARE_START,
                        participant_id=pid,
                        data={},
                    )
                )
                output.append(
                    Event(
                        t=current_t,
                        type=EventType.SCREENSHARE_END,
                        participant_id=pid,
                        data={},
                    )
                )
                continue
            duration = current_t - t_on
            # Same loop-if-shorter/trim-if-longer treatment as webcam - the
            # underlying synthesis mechanism doesn't care whether the source
            # is "a webcam clip" or "a screen recording", only that it needs
            # to exactly fill a known on..off window.
            clip_path = synthesize_webcam_clip(
                src_path, max(duration, 0.1), media_cache_dir
            )
            width, height = ffprobe_video_size(clip_path)
            output.append(
                Event(
                    t=t_on,
                    type=EventType.SCREENSHARE_START,
                    participant_id=pid,
                    data={"width": width, "height": height, "fps": VIDEO_CHUNK_FPS},
                )
            )
            output.extend(
                _video_chunks(clip_path, t_on, media_cache_dir, pid, "screenshare")
            )
            output.append(
                Event(
                    t=current_t,
                    type=EventType.SCREENSHARE_END,
                    participant_id=pid,
                    data={},
                )
            )
            continue

        if ev_type == "audio_stream_on":
            path = data.get("path")
            text = data.get("text")
            if path:
                final_path = resolve_media_path(path, scenario_dir)
                duration = ffprobe_duration(final_path)
            else:
                final_path = synthesize_tts(text, pid, media_cache_dir, driverName)
                duration = ffprobe_duration(final_path)

            pcm_path = extract_audio_pcm(final_path, AUDIO_SAMPLE_RATE, media_cache_dir)

            on_data = {
                "sample_rate": AUDIO_SAMPLE_RATE,
                "encoding": "pcm_s16le",
                "channels": 1,
            }
            if text:
                on_data["text"] = text
            output.append(
                Event(
                    t=current_t,
                    type=EventType.AUDIO_STREAM_ON,
                    participant_id=pid,
                    # marker + codec metadata only - no path. Actual
                    # samples arrive as "stream" chunks between this and
                    # the auto-derived audio_stream_off below.
                    data=on_data,
                )
            )
            output.extend(_audio_chunks(pcm_path, current_t, pid))
            current_t += duration
            output.append(
                Event(
                    t=current_t,
                    type=EventType.AUDIO_STREAM_OFF,
                    participant_id=pid,
                    data={},
                )
            )
            continue

        # all other instantaneous events: don't advance the clock
        output.append(
            Event(t=current_t, type=EventType(ev_type), participant_id=pid, data=data)
        )

    if pending_webcam:
        # validation should have already caught this, but guard anyway
        unclosed = ", ".join(pending_webcam.keys())
        raise ValidationError([f"webcam left on with no webcam_off for: {unclosed}"])

    if pending_screenshare:
        # validation should have already caught this, but guard anyway
        unclosed = ", ".join(pending_screenshare.keys())
        raise ValidationError(
            [f"screenshare left open with no screenshare_end for: {unclosed}"]
        )

    output.sort(key=lambda e: e.t)

    return CompiledScenario(
        metadata=metadata,
        controls=controls,
        evaluation=evaluation,
        context=context,
        participants=participants,
        timeline=output,
        scenario_dir=scenario_dir,
    )


def compile_scenario(
    scenario_dir: str,
    index_filename: str = "index.yml",
    driverName: str | None = None,
) -> CompiledScenario:
    index_path = os.path.join(scenario_dir, index_filename)
    if not os.path.isfile(index_path):
        raise FileNotFoundError(f"no {index_filename} found in {scenario_dir}")

    source_hash = _source_hash(index_path)
    cache_path = os.path.join(scenario_dir, CACHE_DIRNAME, COMPILED_FILENAME)

    if os.path.isfile(cache_path):
        with open(cache_path, "r") as f:
            cached = json.load(f)
        if cached.get("source_hash") == source_hash:
            return _from_jsonable(cached)
        # else: stale, fall through and recompile

    raw = load_yaml(index_path)
    errors = validate(raw, scenario_dir)
    if errors:
        raise ValidationError(errors)

    scenario = _compile_fresh(raw, scenario_dir, driverName)

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(_to_jsonable(scenario, source_hash), f, indent=2)

    return scenario
