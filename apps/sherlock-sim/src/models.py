"""
Core data models for the Scenario Simulator.

These event shapes are deliberately meant to mirror what a real
Meet/Zoom/Teams SDK adapter would emit. The Engine should not be able
to tell whether events came from this simulator or a real meeting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class EventType(str, Enum):
    PARTICIPANT_JOIN = "participant_join"
    PARTICIPANT_LEAVE = "participant_leave"
    WEBCAM_ON = "webcam_on"
    WEBCAM_OFF = "webcam_off"
    SCREENSHARE_START = "screenshare_start"
    SCREENSHARE_END = "screenshare_end"
    SPEAKING_START = "speaking_start"
    SPEAKING_END = "speaking_end"
    TRANSCRIPT_SEGMENT = "transcript_segment"
    MEDIA_STREAM_START = "media_stream_start"
    MEDIA_STREAM_END = "media_stream_end"


@dataclass
class Event:
    """A single emitted event. `t` is seconds from session start."""
    t: float
    type: EventType
    participant_id: Optional[str]
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionContext:
    """Emitted once, at session start. Not part of the timeline."""
    calendar_invite: dict[str, Any]
    interview_schedule: dict[str, Any]
    interviewer_names: list[str]
    candidate_name: str
    candidate_email: str


@dataclass
class Participant:
    participant_id: str
    display_name: str
    role_hint: Optional[str] = None  # "candidate" | "interviewer" | "observer" | None
    audio_path: Optional[str] = None
    video_path: Optional[str] = None


@dataclass
class ScenarioMetadata:
    name: str
    slug: str
    remarks: Optional[str] = None
    ground_truth_participant_id: Optional[str] = None
    speed_multiplier: float = 1.0  # 1.0 = real time, 10.0 = 10x faster playback


@dataclass
class CompiledScenario:
    metadata: ScenarioMetadata
    context: SessionContext
    participants: dict[str, Participant]
    timeline: list[Event]  # sorted by t
    scenario_dir: str
