"""
Compile: validated raw dict -> CompiledScenario, cached as a .compiled
artifact under scenario_dir/.cache/ so repeat runs don't redo TTS
generation or ffmpeg synthesis. Invalidated by a hash of index.yml's
bytes PLUS the (path, mtime, size) of every media file it references
(webcam_on/screenshare_start/audio_stream_on `data.path`) - editing
index.yml OR swapping/re-recording a referenced media file under the
same filename both correctly bust the cache. Hashing mtime+size rather
than full file content mirrors the same cheap-identity approach
media_gen.py already uses for its own per-asset cache keys.

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
import uuid
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

# Bump whenever _to_jsonable/_from_jsonable's shape changes (e.g. a new
# required field like track_id). source_hash alone only tracks the
# *scenario's* identity (index.yml + media files) - it has no way to
# know the *compiler's own output format* changed, so without this a
# pre-existing local .cache/compiled.json from before such a change
# would still hash-match and get loaded, and _from_jsonable would
# KeyError (or worse, silently misparse) instead of just recompiling.
CACHE_SCHEMA_VERSION = 2

# Fixed codec choice, not a scenario knob - unlike video_fps/audio_chunk_ms
# (controls.py), the PCM sample rate isn't something a scenario author has
# a reason to vary per-scenario.
AUDIO_SAMPLE_RATE = 16000


def _new_track_id(pid: str, modality: str) -> str:
    """Globally-unique id for one on..off window. Prefixed with
    pid/modality purely for human readability in logs/dashboards - the
    uniqueness guarantee comes from the uuid4 suffix, not the prefix,
    so nothing may parse the prefix back apart to recover pid/modality."""
    return f"{pid}:{modality}:{uuid.uuid4().hex[:12]}"


def load_yaml(index_path: str) -> dict:
    with open(index_path, "r") as f:
        return yaml.safe_load(f)


def _referenced_media_paths(raw: dict, scenario_dir: str) -> list[str]:
    """Every resolved media path an author could point at from the
    timeline (webcam_on / screenshare_start / audio_stream_on). Used
    only for cache-invalidation identity below - existence isn't
    checked here, that's validate()'s job."""
    paths: list[str] = []
    for ev in raw.get("timeline") or []:
        ev = ev or {}
        if ev.get("type") not in ("webcam_on", "screenshare_start", "audio_stream_on"):
            continue
        path = (ev.get("data") or {}).get("path")
        if path:
            paths.append(resolve_media_path(path, scenario_dir))
    return paths


def _source_hash(index_path: str, raw: dict, scenario_dir: str) -> str:
    """Content hash of index.yml's bytes, plus the identity (path,
    mtime, size) of every media file it references. Either changing
    the yaml OR replacing a referenced media file (same filename, new
    content - e.g. re-recording scenario_dir/media/candidate.mp4)
    must invalidate the cache; hashing index.yml alone misses the
    second case entirely."""
    h = hashlib.sha256()
    with open(index_path, "rb") as f:
        h.update(f.read())
    for path in sorted(_referenced_media_paths(raw, scenario_dir)):
        h.update(path.encode())
        try:
            stat = os.stat(path)
            h.update(f":{stat.st_mtime_ns}:{stat.st_size}".encode())
        except OSError:
            # Missing file - validate() (called right after, on a cache
            # miss) is what actually reports this as a proper error;
            # here we just need the hash to still be well-defined and
            # to change once the file starts/stops existing.
            h.update(b":MISSING")
    return h.hexdigest()


def _to_jsonable(scenario: CompiledScenario, source_hash: str) -> dict:
    return {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
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
                "track_id": e.track_id,
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
                track_id=e["track_id"],
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
    clip_path: str, t_on: float, media_cache_dir: str, pid: str, modality: str,
    fps: float, track_id: str,
) -> list[StreamChunk]:
    """Expand one webcam/screenshare on..off window into per-frame
    StreamChunks. Frames are decoded ONCE (extract_video_frames caches
    the whole batch) - this just assigns timestamps/seq, no ffmpeg call
    per chunk. `track_id` ties every chunk back to the specific on..off
    window it belongs to - `seq` alone resets to 0 each window and is
    not globally unique (e.g. a participant's second webcam_on)."""
    frames = extract_video_frames(clip_path, fps, media_cache_dir)
    return [
        StreamChunk(
            t=t_on + i / fps,
            participant_id=pid,
            modality=modality,
            track_id=track_id,
            seq=i,
            source_path=frame_path,
        )
        for i, frame_path in enumerate(frames)
    ]


def _audio_chunks(
    pcm_path: str, t_on: float, pid: str, chunk_ms: int, track_id: str
) -> list[StreamChunk]:
    """Expand one audio_stream_on..off window into fixed-size raw-PCM
    byte-range StreamChunks, all pointing at the SAME already-decoded
    flat pcm_path (extract_audio_pcm decodes once) - just offset/length
    bookkeeping here, no re-decoding per chunk. `track_id` ties every
    chunk back to this specific utterance - this is what a consumer
    should key on (not `seq`) when reconstructing which audio bytes
    belong to which transcript_segment, since seq resets to 0 every
    time the same participant speaks again."""
    bytes_per_sample = 2  # s16le mono
    chunk_bytes = int(AUDIO_SAMPLE_RATE * (chunk_ms / 1000) * bytes_per_sample)
    total_bytes = os.path.getsize(pcm_path)

    chunks = []
    offset = 0
    i = 0
    while offset < total_bytes:
        length = min(chunk_bytes, total_bytes - offset)
        chunks.append(
            StreamChunk(
                t=t_on + i * (chunk_ms / 1000),
                participant_id=pid,
                modality="audio",
                track_id=track_id,
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
        video_fps=float(ctl.get("video_fps", 5.0)),
        audio_chunk_ms=int(ctl.get("audio_chunk_ms", 200)),
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
        str, tuple[float, str, str]
    ] = {}  # pid -> (t_on, resolved_src_path, track_id)
    pending_screenshare: dict[
        str, tuple[float, Optional[str], str]
    ] = {}  # pid -> (t_on, resolved_src_path or None if marker-only, track_id)
    # pid -> track_id of that participant's most recently opened
    # audio_stream_on window. Used only to stamp a correlating
    # `audio_track_id` onto transcript_segment events below - never sent
    # as its own event. Not cleared on audio_stream_off: that Event is
    # auto-appended in the same processing step as audio_stream_on
    # itself (see below), before the authored transcript_segment that
    # conventionally follows it is reached - clearing here would make
    # that transcript_segment see no open window at all. A pid's next
    # audio_stream_on simply overwrites its entry.
    open_audio_track: dict[str, str] = {}

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
            pending_webcam[pid] = (current_t, resolved_src, _new_track_id(pid, "video"))
            continue

        if ev_type == "webcam_off":
            t_on, src_path, track_id = pending_webcam.pop(pid)
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
                    # `track_id` disambiguates this window from any later
                    # webcam_on..off window for the same participant - the
                    # per-chunk `seq` alone resets to 0 each window.
                    data={
                        "width": width, "height": height, "fps": controls.video_fps,
                        "track_id": track_id,
                    },
                )
            )
            output.extend(
                _video_chunks(
                    clip_path, t_on, media_cache_dir, pid, "video", controls.video_fps,
                    track_id,
                )
            )
            output.append(
                Event(
                    t=current_t, type=EventType.WEBCAM_OFF, participant_id=pid,
                    data={"track_id": track_id},
                )
            )
            continue

        if ev_type == "screenshare_start":
            path = data.get("path")
            resolved_src = resolve_media_path(path, scenario_dir) if path else None
            pending_screenshare[pid] = (
                current_t, resolved_src, _new_track_id(pid, "screenshare")
            )
            continue

        if ev_type == "screenshare_end":
            t_on, src_path, track_id = pending_screenshare.pop(pid)
            if src_path is None:
                # marker-only screenshare (no recorded content) - pass both
                # ends through unchanged, no media synthesis.
                output.append(
                    Event(
                        t=t_on,
                        type=EventType.SCREENSHARE_START,
                        participant_id=pid,
                        data={"track_id": track_id},
                    )
                )
                output.append(
                    Event(
                        t=current_t,
                        type=EventType.SCREENSHARE_END,
                        participant_id=pid,
                        data={"track_id": track_id},
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
                    data={
                        "width": width, "height": height, "fps": controls.video_fps,
                        "track_id": track_id,
                    },
                )
            )
            output.extend(
                _video_chunks(
                    clip_path, t_on, media_cache_dir, pid, "screenshare",
                    controls.video_fps, track_id,
                )
            )
            output.append(
                Event(
                    t=current_t,
                    type=EventType.SCREENSHARE_END,
                    participant_id=pid,
                    data={"track_id": track_id},
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

            track_id = _new_track_id(pid, "audio")
            on_data = {
                "sample_rate": AUDIO_SAMPLE_RATE,
                "encoding": "pcm_s16le",
                "channels": 1,
                # Disambiguates this utterance from any later
                # audio_stream_on..off window for the same participant -
                # `seq` on the stream chunks alone resets to 0 each window.
                "track_id": track_id,
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
            output.extend(
                _audio_chunks(pcm_path, current_t, pid, controls.audio_chunk_ms, track_id)
            )
            open_audio_track[pid] = track_id
            current_t += duration
            output.append(
                Event(
                    t=current_t,
                    type=EventType.AUDIO_STREAM_OFF,
                    participant_id=pid,
                    data={"track_id": track_id},
                )
            )
            # Deliberately NOT popped here: audio_stream_off is
            # auto-derived and appended in this same processing step,
            # before the authored transcript_segment that (by
            # convention) follows this audio_stream_on is even reached
            # in the loop. A participant's *next* audio_stream_on
            # naturally overwrites this entry, so leaving it set is
            # what lets that upcoming transcript_segment still resolve
            # to the audio window it was actually spoken during.
            continue

        if ev_type == "transcript_segment":
            # Stamp the track_id of whichever audio window is currently
            # open for this participant, if any, so a consumer can join
            # transcript -> audio bytes on an explicit shared id instead
            # of inferring it from t coinciding with audio_stream_off.
            # Falls back to no audio_track_id for a transcript authored
            # with no matching audio_stream_on (e.g. text-only fixtures).
            track_id = open_audio_track.get(pid)
            if track_id:
                data = {**data, "audio_track_id": track_id}
            output.append(
                Event(
                    t=current_t, type=EventType.TRANSCRIPT_SEGMENT,
                    participant_id=pid, data=data,
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

    # Loaded up front (cheap - it's a small yaml file) because the cache
    # key itself now depends on which media files index.yml references,
    # not just index.yml's own bytes - see _source_hash.
    raw = load_yaml(index_path)
    source_hash = _source_hash(index_path, raw, scenario_dir)
    cache_path = os.path.join(scenario_dir, CACHE_DIRNAME, COMPILED_FILENAME)

    if os.path.isfile(cache_path):
        with open(cache_path, "r") as f:
            cached = json.load(f)
        if (
            cached.get("source_hash") == source_hash
            and cached.get("cache_schema_version") == CACHE_SCHEMA_VERSION
        ):
            return _from_jsonable(cached)
        # else: stale (index.yml/media changed, or this build's compiler
        # output format itself changed) - fall through and recompile

    errors = validate(raw, scenario_dir)
    if errors:
        raise ValidationError(errors)

    scenario = _compile_fresh(raw, scenario_dir, driverName)

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(_to_jsonable(scenario, source_hash), f, indent=2)

    return scenario
