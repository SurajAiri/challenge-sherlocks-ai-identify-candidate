"""
Compile: validated raw dict -> CompiledScenario ready for the emitter.

Also handles the "intelligent media" requirement: if a participant only
has a video_path and no audio_path, extract audio from the video via
ffmpeg once at compile time (cached next to the source file), so the
emitter can still treat audio as its own independent stream.
"""
from __future__ import annotations

import os
import subprocess
import yaml

from models import (
    CompiledScenario, Event, EventType, Participant,
    ScenarioMetadata, SessionContext,
)
from validator import ValidationError, resolve_media_path, validate


def _extract_audio(video_path: str) -> str:
    """Extract audio track from video_path into a sibling .wav, cached."""
    base, _ = os.path.splitext(video_path)
    out_path = f"{base}.extracted.wav"
    if os.path.isfile(out_path):
        return out_path  # cached from a previous run
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            out_path,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return out_path


def load_yaml(index_path: str) -> dict:
    with open(index_path, "r") as f:
        return yaml.safe_load(f)


def compile_scenario(scenario_dir: str, index_filename: str = "index.yml") -> CompiledScenario:
    index_path = os.path.join(scenario_dir, index_filename)
    if not os.path.isfile(index_path):
        raise FileNotFoundError(f"no {index_filename} found in {scenario_dir}")

    raw = load_yaml(index_path)

    errors = validate(raw, scenario_dir)
    if errors:
        raise ValidationError(errors)

    # --- metadata ---
    md = raw["metadata"]
    metadata = ScenarioMetadata(
        name=md["name"],
        slug=md["slug"],
        remarks=md.get("remarks"),
        ground_truth_participant_id=md.get("ground_truth_participant_id"),
        speed_multiplier=float(md.get("speed_multiplier", 1.0)),
    )

    # --- context ---
    ctx = raw["context"]
    context = SessionContext(
        calendar_invite=ctx.get("calendar_invite", {}),
        interview_schedule=ctx.get("interview_schedule", {}),
        interviewer_names=ctx.get("interviewer_names", []),
        candidate_name=ctx["candidate_name"],
        candidate_email=ctx["candidate_email"],
    )

    # --- participants (+ intelligent audio extraction) ---
    participants: dict[str, Participant] = {}
    for pid, pdata in raw["participants"].items():
        pdata = pdata or {}
        audio_path = pdata.get("audio_path")
        video_path = pdata.get("video_path")

        resolved_video = resolve_media_path(video_path, scenario_dir) if video_path else None
        resolved_audio = resolve_media_path(audio_path, scenario_dir) if audio_path else None

        if resolved_video and not resolved_audio:
            resolved_audio = _extract_audio(resolved_video)

        participants[pid] = Participant(
            participant_id=pid,
            display_name=pdata["display_name"],
            role_hint=pdata.get("role_hint"),
            audio_path=resolved_audio,
            video_path=resolved_video,
        )

    # --- timeline (sorted by t; authoring order in yml doesn't matter) ---
    timeline: list[Event] = []
    for ev in raw["timeline"]:
        data = dict(ev.get("data") or {})
        # normalize any media path in event data too (e.g. media_stream_start)
        if "path" in data:
            data["path"] = resolve_media_path(data["path"], scenario_dir)
        timeline.append(
            Event(
                t=float(ev["t"]),
                type=EventType(ev["type"]),
                participant_id=ev.get("participant_id"),
                data=data,
            )
        )
    timeline.sort(key=lambda e: e.t)

    return CompiledScenario(
        metadata=metadata,
        context=context,
        participants=participants,
        timeline=timeline,
        scenario_dir=scenario_dir,
    )
