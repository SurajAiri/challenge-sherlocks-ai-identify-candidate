"""
Core data models for the Scenario Simulator.

Event shapes mirror what a real Meet/Zoom/Teams SDK adapter would emit.
The Engine should not be able to tell whether events came from this
simulator or a real meeting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class EventType(str, Enum):
    PARTICIPANT_JOIN = "participant_join"
    PARTICIPANT_LEAVE = "participant_leave"
    PARTICIPANT_UPDATE = "participant_update"
    WEBCAM_ON = "webcam_on"
    WEBCAM_OFF = "webcam_off"
    SCREENSHARE_START = "screenshare_start"
    SCREENSHARE_END = "screenshare_end"
    SPEAKING_START = "speaking_start"
    SPEAKING_END = "speaking_end"
    TRANSCRIPT_SEGMENT = "transcript_segment"
    AUDIO_STREAM_ON = "audio_stream_on"
    AUDIO_STREAM_OFF = "audio_stream_off"


# Event types an author is allowed to write in index.yml.
# audio_stream_off is deliberately excluded: it is auto-derived by the
# compiler from measured/generated audio duration, never hand-authored.
# silence is authorable but never emitted downstream - it only advances
# the compiler's clock.
AUTHORABLE_EVENT_TYPES = {
    "participant_join", "participant_leave", "participant_update",
    "webcam_on", "webcam_off", "screenshare_start", "screenshare_end",
    "speaking_start", "speaking_end", "transcript_segment",
    "audio_stream_on", "silence",
}


@dataclass
class Event:
    """A single emitted event. `t` is seconds from session start,
    always resolved by the compiler - never authored directly."""
    t: float
    type: EventType
    participant_id: Optional[str]
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionContext:
    calendar_invite: dict[str, Any]
    interview_schedule: dict[str, Any]
    interviewer_names: list[str]
    candidate_name: str
    candidate_email: str


@dataclass
class Participant:
    """Pure identity. Media lives on events (webcam_on/audio_stream_on),
    not here - a participant's camera/mic can start and stop multiple
    times over a session, so media doesn't belong at this scope."""
    participant_id: str
    display_name: str
    role_hint: Optional[str] = None  # "candidate" | "interviewer" | "observer" | None


@dataclass
class ScenarioMetadata:
    """Pure identity + human-readable framing. Nothing here is grading
    truth and nothing here is a runtime knob - see ScenarioEvaluation
    and ScenarioControls for those."""
    name: str
    slug: str
    description: Optional[str] = None  # what this scenario is, for a human
                                        # reader (dashboard-facing). Replaces
                                        # the old `remarks` field 1:1.


@dataclass
class ScenarioControls:
    """Runtime/playback knobs. Never grading truth, never scenario identity -
    changing these doesn't change what the scenario is testing, only how
    fast/faithfully it plays back."""
    speed_multiplier: float = 1.0
    generate_audio: bool = True  # TTS-generate audio for audio_stream_on
                                  # events that only specify `text`, no path


@dataclass
class ScenarioEvaluation:
    """Grading/dashboard-only metadata. NEVER sent down emit()'s wire
    stream - the Engine must never see this. Only exposed via the
    dedicated evaluation endpoint/CLI, for scoring and for the dashboard
    to show a human what the scenario is designed to stress."""
    ground_truth_participant_id: Optional[str] = None
    difficulty: Optional[int] = None  # 1 (easiest) - 5 (hardest)
    challenging_points: list[str] = field(default_factory=list)
    expected_evidence: dict[str, list[str]] = field(default_factory=dict)
    # expected_evidence keys: "primary", "secondary", "misleading".
    # Values are free-text strings - it's the dashboard's job to decide
    # how to render/compare them, not the simulator's.


@dataclass
class CompiledScenario:
    metadata: ScenarioMetadata
    controls: ScenarioControls
    context: SessionContext
    participants: dict[str, Participant]
    timeline: list[Event]  # fully resolved, absolute t, sorted
    scenario_dir: str
    evaluation: ScenarioEvaluation = field(default_factory=ScenarioEvaluation)
