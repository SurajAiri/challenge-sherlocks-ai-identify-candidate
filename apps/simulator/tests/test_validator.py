"""
Table-driven tests for simulator.validator.validate().

These exercise the exact error strings documented in
docs/SKILL.md's "Common validation-error messages" reference table -
if a message there ever drifts from what the code actually emits
(as happened with the controls.generate_audio / metadata.generate_audio
mismatch this suite was added to catch), these tests should fail.
"""
from __future__ import annotations

import copy
import os

import pytest

from simulator.validator import validate


def _write(scenario_dir: str, name: str, content: bytes = b"\x00") -> str:
    """Create a throwaway media file under scenario_dir and return its
    path relative to scenario_dir (the convention index.yml authors use)."""
    path = os.path.join(scenario_dir, name)
    with open(path, "wb") as f:
        f.write(content)
    return name


@pytest.fixture
def scenario_dir(tmp_path) -> str:
    return str(tmp_path)


@pytest.fixture
def valid_raw(scenario_dir) -> dict:
    """A minimal, fully valid scenario: two participants, a join for
    each, one audio_stream_on/off pair (text-based) per participant.
    No webcam/screenshare - those are exercised in dedicated tests."""
    return {
        "metadata": {"name": "Test", "slug": "test", "description": "A test scenario."},
        "controls": {"generate_audio": True},
        "context": {"candidate_name": "Jane Doe", "candidate_email": "jane@example.com"},
        "participants": {
            "p_interviewer": {"display_name": "Interviewer", "role_hint": "interviewer"},
            "p_candidate": {"display_name": "Jane Doe", "role_hint": "candidate"},
        },
        "timeline": [
            {"type": "participant_join", "participant_id": "p_interviewer",
             "data": {"display_name": "Interviewer"}},
            {"type": "participant_join", "participant_id": "p_candidate",
             "data": {"display_name": "Jane Doe"}},
            {"type": "audio_stream_on", "participant_id": "p_interviewer",
             "data": {"text": "Hello."}},
            {"type": "audio_stream_on", "participant_id": "p_candidate",
             "data": {"text": "Hi there."}},
            {"type": "participant_leave", "participant_id": "p_interviewer"},
            {"type": "participant_leave", "participant_id": "p_candidate"},
        ],
        "evaluation": {"ground_truth_participant_id": "p_candidate", "difficulty": 2},
    }


def test_valid_scenario_has_no_errors(valid_raw, scenario_dir):
    assert validate(valid_raw, scenario_dir) == []


# --- required top-level / metadata / context -------------------------------

@pytest.mark.parametrize("key", ["metadata", "context", "participants", "timeline"])
def test_missing_top_level_section(valid_raw, scenario_dir, key):
    raw = copy.deepcopy(valid_raw)
    del raw[key]
    errors = validate(raw, scenario_dir)
    assert any(f"missing required top-level section: '{key}'" in e for e in errors)


@pytest.mark.parametrize("key", ["name", "slug", "description"])
def test_missing_metadata_field(valid_raw, scenario_dir, key):
    raw = copy.deepcopy(valid_raw)
    del raw["metadata"][key]
    errors = validate(raw, scenario_dir)
    assert f"metadata.{key} is required" in errors


@pytest.mark.parametrize("key", ["candidate_name", "candidate_email"])
def test_missing_context_field(valid_raw, scenario_dir, key):
    raw = copy.deepcopy(valid_raw)
    del raw["context"][key]
    errors = validate(raw, scenario_dir)
    assert f"context.{key} is required" in errors


def test_no_participants_is_an_error(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["participants"] = {}
    errors = validate(raw, scenario_dir)
    assert "participants: at least one participant is required" in errors


def test_participant_missing_display_name(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["participants"]["p_candidate"] = {"role_hint": "candidate"}
    errors = validate(raw, scenario_dir)
    assert "participants.p_candidate.display_name is required" in errors


# --- timeline / event-type checks -------------------------------------------

def test_unknown_event_type(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["timeline"].append({"type": "teleport", "participant_id": "p_candidate"})
    errors = validate(raw, scenario_dir)
    assert any("is not a recognized event type" in e for e in errors)


def test_hand_authored_audio_stream_off_is_rejected(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["timeline"].append({"type": "audio_stream_off", "participant_id": "p_candidate"})
    errors = validate(raw, scenario_dir)
    assert any(
        "'audio_stream_off' must not be hand-authored" in e for e in errors
    )


def test_unknown_participant_id_in_timeline(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["timeline"].append({"type": "speaking_start", "participant_id": "p_ghost"})
    errors = validate(raw, scenario_dir)
    assert any("participant_id 'p_ghost' is not declared" in e for e in errors)


def test_silence_requires_numeric_duration(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["timeline"].append({"type": "silence", "data": {"duration": "a while"}})
    errors = validate(raw, scenario_dir)
    assert any("silence requires numeric data.duration" in e for e in errors)


# --- webcam pairing ----------------------------------------------------------

def test_webcam_on_requires_path(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["timeline"].insert(2, {"type": "webcam_on", "participant_id": "p_candidate", "data": {}})
    errors = validate(raw, scenario_dir)
    assert any("webcam_on requires data.path" in e for e in errors)


def test_webcam_on_path_must_resolve(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["timeline"].insert(
        2,
        {"type": "webcam_on", "participant_id": "p_candidate",
         "data": {"path": "media/does_not_exist.png"}},
    )
    errors = validate(raw, scenario_dir)
    assert any("does not resolve to a file" in e for e in errors)


def test_webcam_double_on_without_off(valid_raw, scenario_dir):
    img = _write(scenario_dir, "cam.png")
    raw = copy.deepcopy(valid_raw)
    raw["timeline"][2:2] = [
        {"type": "webcam_on", "participant_id": "p_candidate", "data": {"path": img}},
        {"type": "webcam_on", "participant_id": "p_candidate", "data": {"path": img}},
    ]
    errors = validate(raw, scenario_dir)
    assert any("webcam is already on" in e for e in errors)


def test_webcam_off_without_on(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["timeline"].insert(2, {"type": "webcam_off", "participant_id": "p_candidate"})
    errors = validate(raw, scenario_dir)
    assert any("webcam was not on" in e for e in errors)


def test_webcam_left_open_at_end(valid_raw, scenario_dir):
    img = _write(scenario_dir, "cam.png")
    raw = copy.deepcopy(valid_raw)
    raw["timeline"].insert(
        2, {"type": "webcam_on", "participant_id": "p_candidate", "data": {"path": img}}
    )
    errors = validate(raw, scenario_dir)
    assert any("was turned on but never turned off" in e for e in errors)


def test_webcam_on_off_valid_pair(valid_raw, scenario_dir):
    img = _write(scenario_dir, "cam.png")
    raw = copy.deepcopy(valid_raw)
    raw["timeline"][2:2] = [
        {"type": "webcam_on", "participant_id": "p_candidate", "data": {"path": img}},
        {"type": "webcam_off", "participant_id": "p_candidate"},
    ]
    assert validate(raw, scenario_dir) == []


# --- screenshare pairing ------------------------------------------------------

def test_screenshare_marker_only_is_valid(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["timeline"][2:2] = [
        {"type": "screenshare_start", "participant_id": "p_candidate"},
        {"type": "screenshare_end", "participant_id": "p_candidate"},
    ]
    assert validate(raw, scenario_dir) == []


def test_screenshare_double_start(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["timeline"][2:2] = [
        {"type": "screenshare_start", "participant_id": "p_candidate"},
        {"type": "screenshare_start", "participant_id": "p_candidate"},
    ]
    errors = validate(raw, scenario_dir)
    assert any("screenshare is already open" in e for e in errors)


def test_screenshare_end_without_start(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["timeline"].insert(2, {"type": "screenshare_end", "participant_id": "p_candidate"})
    errors = validate(raw, scenario_dir)
    assert any("no screenshare was open" in e for e in errors)


def test_screenshare_left_open_at_end(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["timeline"].insert(2, {"type": "screenshare_start", "participant_id": "p_candidate"})
    errors = validate(raw, scenario_dir)
    assert any("was started but never ended" in e for e in errors)


# --- participant_update --------------------------------------------------------

def test_participant_update_requires_a_field(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["timeline"].insert(2, {"type": "participant_update", "participant_id": "p_candidate",
                                "data": {}})
    errors = validate(raw, scenario_dir)
    assert any("requires at least one of" in e for e in errors)


def test_participant_update_empty_display_name_rejected(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["timeline"].insert(
        2,
        {"type": "participant_update", "participant_id": "p_candidate",
         "data": {"display_name": ""}},
    )
    errors = validate(raw, scenario_dir)
    assert any("must be non-empty" in e for e in errors)


def test_participant_update_valid_rename(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["timeline"].insert(
        2,
        {"type": "participant_update", "participant_id": "p_candidate",
         "data": {"display_name": "Jane D."}},
    )
    assert validate(raw, scenario_dir) == []


# --- audio_stream_on / controls.generate_audio ---------------------------------

def test_audio_stream_on_requires_path_or_text(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["timeline"][2] = {"type": "audio_stream_on", "participant_id": "p_interviewer",
                           "data": {}}
    errors = validate(raw, scenario_dir)
    assert any("audio_stream_on requires data.path or data.text" in e for e in errors)


def test_text_only_audio_rejected_when_generate_audio_false(valid_raw, scenario_dir):
    """Regression test for the controls.generate_audio / metadata.generate_audio
    message mismatch: the error must name `controls.generate_audio`, matching
    docs/SKILL.md's documented error-message reference table."""
    raw = copy.deepcopy(valid_raw)
    raw["controls"]["generate_audio"] = False
    errors = validate(raw, scenario_dir)
    assert any(
        "audio_stream_on has only 'text' (no data.path) but controls.generate_audio is false" in e
        for e in errors
    )
    assert not any("metadata.generate_audio" in e for e in errors)


def test_audio_stream_on_with_path_ok_even_if_generate_audio_false(valid_raw, scenario_dir):
    wav = _write(scenario_dir, "line.wav")
    raw = copy.deepcopy(valid_raw)
    raw["controls"]["generate_audio"] = False
    # Replace BOTH audio_stream_on entries (interviewer at index 2, candidate
    # at index 3) with path-based data - leaving either one as text-only
    # would (correctly) trip the generate_audio check and defeat the point
    # of this test.
    raw["timeline"][2] = {"type": "audio_stream_on", "participant_id": "p_interviewer",
                           "data": {"path": wav}}
    raw["timeline"][3] = {"type": "audio_stream_on", "participant_id": "p_candidate",
                           "data": {"path": wav}}
    errors = validate(raw, scenario_dir)
    assert not any("generate_audio" in e for e in errors)


# --- controls validation --------------------------------------------------------

@pytest.mark.parametrize("value", [0, -1.0])
def test_video_fps_must_be_positive(valid_raw, scenario_dir, value):
    raw = copy.deepcopy(valid_raw)
    raw["controls"]["video_fps"] = value
    errors = validate(raw, scenario_dir)
    assert any("controls.video_fps must be a positive number" in e for e in errors)


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_audio_chunk_ms_must_be_a_positive_int(valid_raw, scenario_dir, value):
    raw = copy.deepcopy(valid_raw)
    raw["controls"]["audio_chunk_ms"] = value
    errors = validate(raw, scenario_dir)
    assert any("controls.audio_chunk_ms must be a positive integer" in e for e in errors)


# --- evaluation section ----------------------------------------------------------

def test_ground_truth_must_be_a_declared_participant(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["evaluation"]["ground_truth_participant_id"] = "p_ghost"
    errors = validate(raw, scenario_dir)
    assert any("is not a declared participant" in e for e in errors)


@pytest.mark.parametrize("value", [0, 6, "3", True])
def test_difficulty_must_be_int_1_to_5(valid_raw, scenario_dir, value):
    raw = copy.deepcopy(valid_raw)
    raw["evaluation"]["difficulty"] = value
    errors = validate(raw, scenario_dir)
    assert any("difficulty must be an integer 1 (easiest) - 5 (hardest)" in e for e in errors)


def test_expected_evidence_rejects_unknown_keys(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["evaluation"]["expected_evidence"] = {"primary": ["x"], "bogus": ["y"]}
    errors = validate(raw, scenario_dir)
    assert any("unrecognized key(s)" in e and "bogus" in e for e in errors)


def test_expected_evidence_valid_keys_pass(valid_raw, scenario_dir):
    raw = copy.deepcopy(valid_raw)
    raw["evaluation"]["expected_evidence"] = {
        "primary": ["a"], "secondary": ["b"], "misleading": ["c"],
    }
    assert validate(raw, scenario_dir) == []
