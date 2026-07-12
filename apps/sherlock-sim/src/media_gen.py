"""
Media synthesis for the compiler.

Two jobs:
  1. Given a webcam_on..webcam_off window of known duration and a source
     image or video, produce a clip that exactly fills that window
     (loop if shorter, trim if longer - same treatment for both, since
     it's the same ffmpeg mechanism either way).
  2. Given text for an audio_stream_on event, synthesize speech via
     offline TTS (pyttsx3/espeak - no network dependency), with a
     distinct, deterministic voice per participant.

Everything here is cached in the scenario's `.cache/media/` dir, keyed
by a hash of its inputs, so repeat compiles don't regenerate.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def is_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS


def ffprobe_duration(path: str) -> float:
    """Robust duration probe with fallbacks, because `format=duration`
    is frequently absent even on valid media: non-faststart mp4s, raw
    AAC/ADTS, some AIFF variants (notably files written by pyttsx3's
    macOS `nsss` driver), etc. all report "N/A" at the container level
    even though the audio/video itself is fine."""
    # 1. container-level duration (fast path, works for most files)
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
    )
    val = out.stdout.strip()
    if val and val != "N/A":
        try:
            return float(val)
        except ValueError:
            pass

    # 2. stream-level duration (present on some files that lack it at
    # the format level - e.g. raw AAC, some AIFF/MPEG-TS variants)
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "0",
            "-show_entries",
            "stream=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
    )
    val = out.stdout.strip()
    if val and val != "N/A":
        try:
            return float(val)
        except ValueError:
            pass

    # 3. last resort: actually decode the whole file and read back the
    # wall-clock time ffmpeg reports having processed. Always works
    # regardless of what the container header claims, at the cost of
    # a full decode pass.
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    match = re.search(r"time=(\d+):(\d\d):(\d\d\.\d+)", out.stderr)
    if match:
        h, m, s = match.groups()
        return int(h) * 3600 + int(m) * 60 + float(s)

    raise RuntimeError(
        f"could not determine duration for '{path}' via ffprobe or ffmpeg decode "
        f"(format/stream duration both N/A, no time= progress found either)"
    )


def _cache_path(cache_dir: str, key: str, ext: str) -> str:
    os.makedirs(cache_dir, exist_ok=True)
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return os.path.join(cache_dir, f"{digest}{ext}")


def synthesize_webcam_clip(src_path: str, duration: float, cache_dir: str) -> str:
    """Produce a clip exactly `duration` seconds long from an image or
    video source, looping if the source is shorter, trimming if longer.
    Cached by (src_path mtime+size, duration)."""
    stat = os.stat(src_path)
    key = f"webcam:{src_path}:{stat.st_mtime_ns}:{stat.st_size}:{duration:.3f}"
    out_path = _cache_path(cache_dir, key, ".mp4")
    if os.path.isfile(out_path):
        return out_path

    if is_image(src_path):
        cmd = [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            src_path,
            "-t",
            f"{duration:.3f}",
            "-r",
            "5",
            "-pix_fmt",
            "yuv420p",
            out_path,
        ]
    else:
        # -stream_loop -1 loops the input indefinitely; -t then trims
        # the result to the target duration regardless of whether the
        # source was originally shorter or longer than that duration.
        cmd = [
            "ffmpeg",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            src_path,
            "-t",
            f"{duration:.3f}",
            "-c",
            "copy",
            out_path,
        ]
    subprocess.run(
        cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return out_path


# deterministic per-participant voice assignment (offline, via espeak-ng
# voices exposed through pyttsx3 - no cloud dependency, no restriction
# against swapping in a cloud TTS later behind this same function).
_VOICE_POOL_CACHE: list[str] | None = None


def _voice_pool() -> list[str]:
    global _VOICE_POOL_CACHE
    if _VOICE_POOL_CACHE is None:
        import pyttsx3

        engine = pyttsx3.init()
        _VOICE_POOL_CACHE = [v.id for v in engine.getProperty("voices")]
        engine.stop()
    return _VOICE_POOL_CACHE


def _voice_for(participant_id: str) -> tuple[str, int]:
    """Returns (voice_id, rate) deterministically derived from participant_id,
    so the same participant always gets the same voice across compiles,
    and different participants are audibly distinguishable."""
    pool = _voice_pool()
    h = int(hashlib.md5(participant_id.encode()).hexdigest(), 16)
    voice_id = pool[h % len(pool)]
    rate = 150 + (h % 40)  # small deterministic rate variation, 150-190 wpm
    return voice_id, rate


def synthesize_tts(text: str, participant_id: str, cache_dir: str) -> str:
    """Generate speech audio for `text` in a voice deterministically
    assigned to `participant_id`. Cached by (participant_id, text)."""
    key = f"tts:{participant_id}:{text}"
    out_path = _cache_path(cache_dir, key, ".wav")
    if os.path.isfile(out_path):
        if os.path.getsize(out_path) > 0:
            return out_path
        # a prior run crashed mid-write (e.g. the async-flush race this
        # function now guards against below) and left a 0-byte file
        # cached under this key - don't trust it, regenerate.
        os.remove(out_path)

    import pyttsx3

    voice_id, rate = _voice_for(participant_id)
    engine = pyttsx3.init()
    engine.setProperty("voice", voice_id)
    engine.setProperty("rate", rate)
    engine.save_to_file(text, out_path)
    engine.runAndWait()
    engine.stop()

    if not os.path.isfile(out_path):
        raise RuntimeError(f"TTS generation failed for participant '{participant_id}'")

    # On macOS, pyttsx3's `nsss` driver (NSSpeechSynthesizer) writes the
    # file asynchronously via Core Audio - runAndWait() returning is not
    # a guarantee the file is fully flushed/finalized yet. Writing a
    # near-empty or still-growing file straight into ffprobe is exactly
    # what produces duration="N/A" downstream. Poll until the size stops
    # changing (or bail with a clear error) before handing it off.
    last_size = -1
    stable_reads = 0
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        size = os.path.getsize(out_path)
        if size > 0 and size == last_size:
            stable_reads += 1
            if stable_reads >= 2:
                break
        else:
            stable_reads = 0
        last_size = size
        time.sleep(0.1)
    else:
        raise RuntimeError(
            f"TTS output for participant '{participant_id}' never finished "
            f"writing (still growing/empty after 5s): {out_path}"
        )

    return out_path
