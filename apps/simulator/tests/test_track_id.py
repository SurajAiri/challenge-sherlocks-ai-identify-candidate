"""
Regression tests for track_id propagation.

Exercises the real _compile_fresh() pipeline (not just hashing, like
test_compiler_cache.py) but stubs out every media_gen call so this runs
without ffmpeg/espeak-ng, same "hashing-only tests run anywhere" spirit
as test_compiler_cache.py - here we care about timeline/track_id shape,
not real synthesized bytes.

Covers the concrete gap: two on..off windows for the same
participant_id+modality reset `seq` to 0, so (participant_id, modality,
seq) is NOT a safe key across a session - only track_id is. And
transcript_segment must be stamped with the audio_track_id of whichever
audio_stream_on window was open when it was authored, so a consumer can
join transcript -> audio bytes on an explicit id instead of inferring
it from timestamp coincidence.
"""
from __future__ import annotations

import os

import pytest

from simulator import compiler as compiler_mod
from simulator.models import EventType, StreamChunk


@pytest.fixture(autouse=True)
def _stub_media_gen(tmp_path, monkeypatch):
    """Replace every ffmpeg/TTS-backed media_gen call compiler.py uses
    with cheap deterministic stand-ins, so _compile_fresh runs without
    real media tools installed."""
    pcm_counter = {"n": 0}

    def fake_ffprobe_duration(path):
        return 1.0  # 1 second of "audio" per audio_stream_on window

    def fake_synthesize_tts(text, pid, cache_dir, driverName=None):
        return os.path.join(str(tmp_path), f"{pid}-{pcm_counter['n']}.wav")

    def fake_extract_audio_pcm(src_path, sample_rate, cache_dir):
        # one fixed-size flat PCM file per call - enough bytes for a
        # couple of audio_chunk_ms-sized chunks at the default chunk_ms
        pcm_counter["n"] += 1
        path = os.path.join(str(tmp_path), f"pcm-{pcm_counter['n']}.raw")
        with open(path, "wb") as f:
            f.write(b"\x00" * (16000 * 2))  # 1s of s16le mono silence
        return path

    def fake_synthesize_webcam_clip(src_path, duration, cache_dir):
        return src_path or "unused.png"

    def fake_ffprobe_video_size(path):
        return (640, 480)

    def fake_extract_video_frames(clip_path, fps, cache_dir):
        # pretend a 1-frame clip regardless of duration - shape, not
        # realism, is what these tests check
        return ["frame0.png"]

    monkeypatch.setattr(compiler_mod, "ffprobe_duration", fake_ffprobe_duration)
    monkeypatch.setattr(compiler_mod, "synthesize_tts", fake_synthesize_tts)
    monkeypatch.setattr(compiler_mod, "extract_audio_pcm", fake_extract_audio_pcm)
    monkeypatch.setattr(compiler_mod, "synthesize_webcam_clip", fake_synthesize_webcam_clip)
    monkeypatch.setattr(compiler_mod, "ffprobe_video_size", fake_ffprobe_video_size)
    monkeypatch.setattr(compiler_mod, "extract_video_frames", fake_extract_video_frames)


def _base_raw(timeline: list[dict]) -> dict:
    return {
        "metadata": {"name": "t", "slug": "t"},
        "controls": {},
        "context": {
            "candidate_name": "C", "candidate_email": "c@example.com",
            "interviewer_names": [], "calendar_invite": {}, "interview_schedule": {},
        },
        "participants": {"p1": {"display_name": "P1"}},
        "timeline": timeline,
    }


def test_two_audio_windows_same_participant_get_distinct_track_ids(tmp_path):
    raw = _base_raw([
        {"type": "speaking_start", "participant_id": "p1"},
        {"type": "audio_stream_on", "participant_id": "p1", "data": {"text": "hello"}},
        {"type": "transcript_segment", "participant_id": "p1", "data": {"text": "hello"}},
        {"type": "speaking_end", "participant_id": "p1"},
        {"type": "speaking_start", "participant_id": "p1"},
        {"type": "audio_stream_on", "participant_id": "p1", "data": {"text": "again"}},
        {"type": "transcript_segment", "participant_id": "p1", "data": {"text": "again"}},
        {"type": "speaking_end", "participant_id": "p1"},
    ])
    scenario = compiler_mod._compile_fresh(raw, str(tmp_path))

    audio_on_events = [
        e for e in scenario.timeline
        if hasattr(e, "type") and e.type == EventType.AUDIO_STREAM_ON
    ]
    assert len(audio_on_events) == 2
    track_id_1, track_id_2 = audio_on_events[0].data["track_id"], audio_on_events[1].data["track_id"]
    assert track_id_1 != track_id_2, "two distinct utterances must not share a track_id"

    # every StreamChunk for a given window must carry that window's track_id,
    # and seq alone (0-based per window) collides across windows - this is
    # exactly the ambiguity track_id exists to resolve
    chunks = [e for e in scenario.timeline if isinstance(e, StreamChunk) and e.modality == "audio"]
    assert chunks, "expected at least one audio StreamChunk"
    window_1_chunks = [c for c in chunks if c.track_id == track_id_1]
    window_2_chunks = [c for c in chunks if c.track_id == track_id_2]
    assert window_1_chunks and window_2_chunks
    # seq collides across windows (both start at 0) - proving seq alone
    # is NOT a safe cross-session key, track_id is what disambiguates
    assert window_1_chunks[0].seq == window_2_chunks[0].seq == 0

    # audio_stream_off echoes the same track_id as the matching _on
    audio_off_events = [
        e for e in scenario.timeline
        if hasattr(e, "type") and e.type == EventType.AUDIO_STREAM_OFF
    ]
    assert {e.data["track_id"] for e in audio_off_events} == {track_id_1, track_id_2}


def test_transcript_segment_stamped_with_open_audio_track_id(tmp_path):
    raw = _base_raw([
        {"type": "speaking_start", "participant_id": "p1"},
        {"type": "audio_stream_on", "participant_id": "p1", "data": {"text": "hello"}},
        {"type": "transcript_segment", "participant_id": "p1", "data": {"text": "hello"}},
        {"type": "speaking_end", "participant_id": "p1"},
        {"type": "speaking_start", "participant_id": "p1"},
        {"type": "audio_stream_on", "participant_id": "p1", "data": {"text": "again"}},
        {"type": "transcript_segment", "participant_id": "p1", "data": {"text": "again"}},
        {"type": "speaking_end", "participant_id": "p1"},
    ])
    scenario = compiler_mod._compile_fresh(raw, str(tmp_path))

    audio_on = [e for e in scenario.timeline if hasattr(e, "type") and e.type == EventType.AUDIO_STREAM_ON]
    transcripts = [e for e in scenario.timeline if hasattr(e, "type") and e.type == EventType.TRANSCRIPT_SEGMENT]
    assert len(audio_on) == len(transcripts) == 2

    # each transcript_segment's audio_track_id must point at the audio
    # window that was actually open when it was authored, by content
    # ("hello" transcript -> "hello" audio window), not just position
    by_text_audio = {e.data.get("text"): e.data["track_id"] for e in audio_on}
    for t in transcripts:
        assert t.data["audio_track_id"] == by_text_audio[t.data["text"]]


def test_transcript_segment_with_no_open_audio_window_has_no_audio_track_id(tmp_path):
    raw = _base_raw([
        {"type": "transcript_segment", "participant_id": "p1", "data": {"text": "text-only"}},
    ])
    scenario = compiler_mod._compile_fresh(raw, str(tmp_path))
    transcripts = [e for e in scenario.timeline if hasattr(e, "type") and e.type == EventType.TRANSCRIPT_SEGMENT]
    assert len(transcripts) == 1
    assert "audio_track_id" not in transcripts[0].data


def test_two_webcam_windows_same_participant_get_distinct_track_ids(tmp_path):
    raw = _base_raw([
        {"type": "webcam_on", "participant_id": "p1", "data": {"path": "a.png"}},
        {"type": "silence", "data": {"duration": 1}},
        {"type": "webcam_off", "participant_id": "p1"},
        {"type": "webcam_on", "participant_id": "p1", "data": {"path": "a.png"}},
        {"type": "silence", "data": {"duration": 1}},
        {"type": "webcam_off", "participant_id": "p1"},
    ])
    scenario = compiler_mod._compile_fresh(raw, str(tmp_path))
    webcam_on = [e for e in scenario.timeline if hasattr(e, "type") and e.type == EventType.WEBCAM_ON]
    webcam_off = [e for e in scenario.timeline if hasattr(e, "type") and e.type == EventType.WEBCAM_OFF]
    assert len(webcam_on) == 2
    ids_on = [e.data["track_id"] for e in webcam_on]
    ids_off = [e.data["track_id"] for e in webcam_off]
    assert ids_on[0] != ids_on[1]
    assert set(ids_on) == set(ids_off)

    video_chunks = [e for e in scenario.timeline if isinstance(e, StreamChunk) and e.modality == "video"]
    assert {c.track_id for c in video_chunks} == set(ids_on)
