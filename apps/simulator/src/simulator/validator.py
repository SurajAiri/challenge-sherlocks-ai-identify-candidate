"""
Validation: is index.yml well-formed enough to compile?

Common-sense level checks only: required fields present, references
resolve, media paths that ARE given resolve to real files. Optional
fields may be null. No schema-framework.
"""

from __future__ import annotations

import os
from typing import Any

from simulator.models import AUTHORABLE_EVENT_TYPES


class ValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(f"  - {e}" for e in errors))


REQUIRED_TOP_LEVEL = ["metadata", "context", "participants", "timeline"]
REQUIRED_METADATA = ["name", "slug", "description"]
REQUIRED_CONTEXT = ["candidate_name", "candidate_email"]
EXPECTED_EVIDENCE_KEYS = {"primary", "secondary", "misleading"}


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
        return errors

    # --- metadata ---
    metadata = raw["metadata"] or {}
    for key in REQUIRED_METADATA:
        if not metadata.get(key):
            errors.append(f"metadata.{key} is required")

    # --- controls (optional section - both fields have defaults) ---
    controls = raw.get("controls") or {}
    generate_audio = controls.get("generate_audio", True)
    if not isinstance(generate_audio, bool):
        errors.append("controls.generate_audio must be true or false")
    speed_multiplier = controls.get("speed_multiplier", 1.0)
    if not isinstance(speed_multiplier, (int, float)):
        errors.append("controls.speed_multiplier must be a number")

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

    # --- timeline ---
    timeline = raw["timeline"] or []
    if not timeline:
        errors.append("timeline: must contain at least one event")

    # webcam pairing state: participant_id -> currently open (bool)
    webcam_open: dict[str, bool] = {}
    # screenshare pairing state: participant_id -> currently open (bool)
    screenshare_open: dict[str, bool] = {}

    for i, ev in enumerate(timeline):
        ev = ev or {}
        loc = f"timeline[{i}]"

        ev_type = ev.get("type")
        if not ev_type:
            errors.append(f"{loc}.type is required")
            continue
        if ev_type not in AUTHORABLE_EVENT_TYPES:
            if ev_type == "audio_stream_off":
                errors.append(
                    f"{loc}: 'audio_stream_off' must not be hand-authored - "
                    f"the compiler derives it automatically from audio duration"
                )
            else:
                errors.append(f"{loc}.type '{ev_type}' is not a recognized event type")
            continue

        pid = ev.get("participant_id")
        if ev_type != "silence" and pid is None:
            errors.append(f"{loc}: '{ev_type}' requires participant_id")
        if pid is not None and pid not in known_ids:
            errors.append(
                f"{loc}.participant_id '{pid}' is not declared in participants"
            )

        data = ev.get("data") or {}

        if ev_type == "silence":
            if not isinstance(data.get("duration"), (int, float)):
                errors.append(f"{loc}: silence requires numeric data.duration")

        elif ev_type == "webcam_on":
            if webcam_open.get(pid):
                errors.append(
                    f"{loc}: webcam_on for '{pid}' but webcam is already on "
                    f"(missing webcam_off before this)"
                )
            path = data.get("path")
            if not path:
                errors.append(f"{loc}: webcam_on requires data.path (image or video)")
            else:
                resolved = resolve_media_path(path, scenario_dir)
                if not os.path.isfile(resolved):
                    errors.append(
                        f"{loc}: data.path does not resolve to a file: "
                        f"'{path}' -> '{resolved}'"
                    )
            webcam_open[pid] = True

        elif ev_type == "webcam_off":
            if not webcam_open.get(pid):
                errors.append(f"{loc}: webcam_off for '{pid}' but webcam was not on")
            webcam_open[pid] = False

        elif ev_type == "participant_update":
            # Represents an in-session identity change (e.g. display name
            # edited, or a corrected role_hint) for a participant who has
            # already joined - NOT a new participant_join. At least one
            # updatable field must be given, and if display_name is given
            # it must be non-empty (same rule as participants.*.display_name).
            has_display_name = "display_name" in data
            has_role_hint = "role_hint" in data
            if not (has_display_name or has_role_hint):
                errors.append(
                    f"{loc}: participant_update requires at least one of "
                    f"data.display_name or data.role_hint"
                )
            if has_display_name and not data.get("display_name"):
                errors.append(f"{loc}: participant_update data.display_name, if given, "
                               f"must be non-empty")

        elif ev_type == "screenshare_start":
            if screenshare_open.get(pid):
                errors.append(
                    f"{loc}: screenshare_start for '{pid}' but a screenshare "
                    f"is already open (missing screenshare_end before this)"
                )
            # Media is optional for screenshare (unlike webcam): a bare
            # start/end pair with no data.path is a valid "shared their
            # screen, content not modeled" marker. If a path IS given, it
            # is validated and loop-fit exactly like webcam media.
            path = data.get("path")
            if path:
                resolved = resolve_media_path(path, scenario_dir)
                if not os.path.isfile(resolved):
                    errors.append(
                        f"{loc}: data.path does not resolve to a file: "
                        f"'{path}' -> '{resolved}'"
                    )
            screenshare_open[pid] = True

        elif ev_type == "screenshare_end":
            if not screenshare_open.get(pid):
                errors.append(
                    f"{loc}: screenshare_end for '{pid}' but no screenshare was open"
                )
            screenshare_open[pid] = False

        elif ev_type == "audio_stream_on":
            path = data.get("path")
            text = data.get("text")
            if path:
                resolved = resolve_media_path(path, scenario_dir)
                if not os.path.isfile(resolved):
                    errors.append(
                        f"{loc}: data.path does not resolve to a file: "
                        f"'{path}' -> '{resolved}'"
                    )
            elif text:
                if not generate_audio:
                    errors.append(
                        f"{loc}: audio_stream_on has only 'text' (no data.path) "
                        f"but metadata.generate_audio is false"
                    )
            else:
                errors.append(f"{loc}: audio_stream_on requires data.path or data.text")

    for pid, still_open in webcam_open.items():
        if still_open:
            errors.append(
                f"timeline: webcam for '{pid}' was turned on but never turned off"
            )

    for pid, still_open in screenshare_open.items():
        if still_open:
            errors.append(
                f"timeline: screenshare for '{pid}' was started but never ended"
            )

    # --- evaluation (optional section - grading/dashboard-only, never
    # sent down the emit() wire stream; see ScenarioEvaluation) ---
    evaluation = raw.get("evaluation") or {}

    gt = evaluation.get("ground_truth_participant_id")
    if gt and gt not in known_ids:
        errors.append(
            f"evaluation.ground_truth_participant_id '{gt}' is not a declared participant"
        )

    difficulty = evaluation.get("difficulty")
    if difficulty is not None:
        if not isinstance(difficulty, int) or isinstance(difficulty, bool) or not (1 <= difficulty <= 5):
            errors.append("evaluation.difficulty must be an integer 1 (easiest) - 5 (hardest)")

    challenging_points = evaluation.get("challenging_points")
    if challenging_points is not None:
        if not isinstance(challenging_points, list) or not all(
            isinstance(x, str) for x in challenging_points
        ):
            errors.append("evaluation.challenging_points must be a list of strings")

    expected_evidence = evaluation.get("expected_evidence")
    if expected_evidence is not None:
        if not isinstance(expected_evidence, dict):
            errors.append("evaluation.expected_evidence must be a map")
        else:
            unknown_keys = set(expected_evidence.keys()) - EXPECTED_EVIDENCE_KEYS
            if unknown_keys:
                errors.append(
                    f"evaluation.expected_evidence has unrecognized key(s) "
                    f"{sorted(unknown_keys)} - allowed: {sorted(EXPECTED_EVIDENCE_KEYS)}"
                )
            for key, values in expected_evidence.items():
                if key not in EXPECTED_EVIDENCE_KEYS:
                    continue  # already reported above
                if not isinstance(values, list) or not all(
                    isinstance(v, str) for v in values
                ):
                    errors.append(
                        f"evaluation.expected_evidence.{key} must be a list of strings"
                    )

    return errors
