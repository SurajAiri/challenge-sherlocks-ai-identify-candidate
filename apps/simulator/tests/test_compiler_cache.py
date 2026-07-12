"""
Regression tests for cache-invalidation hashing (compiler._source_hash /
_referenced_media_paths). These only exercise the hashing logic itself -
not the full compile pipeline (which needs ffmpeg/espeak-ng) - so they
run anywhere Python + pyyaml are available.
"""
from __future__ import annotations

import os
import time

from simulator.compiler import _referenced_media_paths, _source_hash


def _index_yml(scenario_dir: str) -> str:
    path = os.path.join(scenario_dir, "index.yml")
    with open(path, "w") as f:
        f.write("placeholder: true\n")
    return path


def _media(scenario_dir: str, name: str, content: bytes) -> str:
    path = os.path.join(scenario_dir, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return path


def test_referenced_media_paths_covers_all_three_event_types(tmp_path):
    scenario_dir = str(tmp_path)
    raw = {
        "timeline": [
            {"type": "webcam_on", "data": {"path": "a.png"}},
            {"type": "screenshare_start", "data": {"path": "b.mp4"}},
            {"type": "audio_stream_on", "data": {"path": "c.wav"}},
            {"type": "audio_stream_on", "data": {"text": "no path here"}},
            {"type": "silence", "data": {"duration": 1}},
        ]
    }
    paths = _referenced_media_paths(raw, scenario_dir)
    assert paths == [
        os.path.join(scenario_dir, "a.png"),
        os.path.join(scenario_dir, "b.mp4"),
        os.path.join(scenario_dir, "c.wav"),
    ]


def test_hash_changes_when_index_yml_changes(tmp_path):
    scenario_dir = str(tmp_path)
    index_path = _index_yml(scenario_dir)
    raw = {"timeline": []}
    h1 = _source_hash(index_path, raw, scenario_dir)

    with open(index_path, "a") as f:
        f.write("more: stuff\n")
    h2 = _source_hash(index_path, raw, scenario_dir)

    assert h1 != h2


def test_hash_changes_when_referenced_media_file_changes(tmp_path):
    """The bug this test guards against: swapping a referenced media
    file's *content* (same filename) used to be invisible to the cache
    because only index.yml's bytes were hashed."""
    scenario_dir = str(tmp_path)
    index_path = _index_yml(scenario_dir)
    media_path = _media(scenario_dir, "media/cam.png", b"original bytes")
    raw = {"timeline": [{"type": "webcam_on", "data": {"path": "media/cam.png"}}]}

    h1 = _source_hash(index_path, raw, scenario_dir)

    # Re-record the same file with different content and a later mtime -
    # index.yml itself is untouched.
    time.sleep(0.01)
    with open(media_path, "wb") as f:
        f.write(b"re-recorded bytes, different length")

    h2 = _source_hash(index_path, raw, scenario_dir)
    assert h1 != h2


def test_hash_stable_when_nothing_changes(tmp_path):
    scenario_dir = str(tmp_path)
    index_path = _index_yml(scenario_dir)
    _media(scenario_dir, "media/cam.png", b"bytes")
    raw = {"timeline": [{"type": "webcam_on", "data": {"path": "media/cam.png"}}]}

    h1 = _source_hash(index_path, raw, scenario_dir)
    h2 = _source_hash(index_path, raw, scenario_dir)
    assert h1 == h2


def test_hash_well_defined_when_referenced_media_missing(tmp_path):
    """A scenario referencing a not-yet-existing file shouldn't crash
    hashing - validate() is what reports this as a real error, on the
    subsequent cache-miss path."""
    scenario_dir = str(tmp_path)
    index_path = _index_yml(scenario_dir)
    raw = {"timeline": [{"type": "webcam_on", "data": {"path": "media/missing.png"}}]}

    h = _source_hash(index_path, raw, scenario_dir)
    assert isinstance(h, str) and len(h) == 64  # sha256 hexdigest
