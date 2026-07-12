"""
Validation: is index.yml well-formed enough to compile?

Deliberately "common sense" level checks only, per the actual requirement:
- required sections/fields present
- participant_id references in timeline resolve to declared participants
- media paths resolve to real files (relative-to-scenario-dir OR absolute)
- optional fields may be null, that's fine
No schema-framework, no strict typing library. Plain assertions with
readable error messages, collected (not fail-fast) so you see every
problem in one pass instead of fixing one error at a time.
"""
from __future__ import annotations

import os
from typing import Any


class ValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(f"  - {e}" for e in errors))


REQUIRED_TOP_LEVEL = ["metadata", "context", "participants", "timeline"]
REQUIRED_METADATA = ["name", "slug"]
REQUIRED_CONTEXT = ["candidate_name", "candidate_email"]
VALID_EVENT_TYPES = {
    "participant_join", "participant_leave", "webcam_on", "webcam_off",
    "screenshare_start", "screenshare_end", "speaking_start", "speaking_end",
    "transcript_segment", "media_stream_start", "media_stream_end",
}


def resolve_media_path(path: str, scenario_dir: str) -> str:
    """Absolute paths used as-is; relative paths resolve against scenario_dir."""
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(scenario_dir, path))


def validate(raw: dict[str, Any], scenario_dir: str) -> list[str]:
    """Returns list of errors. Empty list = valid."""
    errors: list[str] = []

    # --- top-level sections ---
    for key in REQUIRED_TOP_LEVEL:
        if key not in raw:
            errors.append(f"missing required top-level section: '{key}'")
    if errors:
        return errors  # can't check anything else without these

    # --- metadata ---
    metadata = raw["metadata"] or {}
    for key in REQUIRED_METADATA:
        if not metadata.get(key):
            errors.append(f"metadata.{key} is required")

    # --- context ---
    context = raw["context"] or {}
    for key in REQUIRED_CONTEXT:
        if not context.get(key):
            errors.append(f"context.{key} is required")

    # --- participants ---
    participants = raw["participants"] or {}
    if not participants:
        errors.append("participants: at least one participant is required")

    known_ids = set(participants.keys())
    for pid, pdata in participants.items():
        pdata = pdata or {}
        if not pdata.get("display_name"):
            errors.append(f"participants.{pid}.display_name is required")
        for media_key in ("audio_path", "video_path"):
            path = pdata.get(media_key)
            if path:
                resolved = resolve_media_path(path, scenario_dir)
                if not os.path.isfile(resolved):
                    errors.append(
                        f"participants.{pid}.{media_key} does not resolve to a "
                        f"file: '{path}' -> '{resolved}'"
                    )

    # --- timeline ---
    timeline = raw["timeline"] or []
    if not timeline:
        errors.append("timeline: must contain at least one event")

    for i, ev in enumerate(timeline):
        ev = ev or {}
        loc = f"timeline[{i}]"
        if "t" not in ev:
            errors.append(f"{loc}.t is required")
        elif not isinstance(ev["t"], (int, float)):
            errors.append(f"{loc}.t must be numeric (seconds offset)")

        ev_type = ev.get("type")
        if not ev_type:
            errors.append(f"{loc}.type is required")
        elif ev_type not in VALID_EVENT_TYPES:
            errors.append(f"{loc}.type '{ev_type}' is not a recognized event type")

        pid = ev.get("participant_id")
        if pid is not None and pid not in known_ids:
            errors.append(
                f"{loc}.participant_id '{pid}' is not declared in participants"
            )

        # media_stream_start must carry a resolvable media path in data
        if ev_type == "media_stream_start":
            data = ev.get("data") or {}
            media_path = data.get("path")
            if not media_path:
                errors.append(f"{loc}: media_stream_start requires data.path")
            else:
                resolved = resolve_media_path(media_path, scenario_dir)
                if not os.path.isfile(resolved):
                    errors.append(
                        f"{loc}: data.path does not resolve to a file: "
                        f"'{media_path}' -> '{resolved}'"
                    )

    # --- ground truth sanity (optional but if present, must be valid) ---
    gt = metadata.get("ground_truth_participant_id")
    if gt and gt not in known_ids:
        errors.append(
            f"metadata.ground_truth_participant_id '{gt}' is not a declared participant"
        )

    return errors
