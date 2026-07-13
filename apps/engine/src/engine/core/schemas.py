"""
Wire-format schemas for the Engine.

These mirror `apps/web/src/lib/types.ts` (which itself mirrors
`apps/simulator/src/simulator/models.py` + `emitter.py`) field-for-field.
The Engine must not be able to tell whether frames came from the
simulator or a real Meet/Zoom/Teams adapter - so this module is the
single place that knows what a frame looks like on the wire, and
everything downstream (event bus, state store, identifiers) works with
these typed objects, never raw dicts.

Kept intentionally close to the TS source of truth. If the dashboard's
`types.ts` changes, this file is the first thing to update.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Event / stream primitives (matches apps/web/src/lib/types.ts EVENT_TYPES)
# ---------------------------------------------------------------------------


class SimEventType(str, Enum):
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


class SimEvent(BaseModel):
    t: float
    type: SimEventType
    participant_id: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)


Modality = Literal["audio", "video", "screenshare"]


class StreamFrame(BaseModel):
    t: float
    participant_id: str
    modality: Modality
    # Globally-unique id for the on..off window this chunk belongs to.
    # See models.py note on the simulator side - `seq` alone is NOT a
    # safe key across a session, always key on track_id (+ seq/t).
    track_id: str
    seq: int
    data: str  # base64-encoded chunk bytes - NOT decoded at this layer.
    # Media decoding/inference (deepfake CV, voice analysis, etc.) is
    # explicitly out of scope for the base engine layer; identifiers
    # that need pixels/samples decode `data` themselves.


class SessionContext(BaseModel):
    calendar_invite: dict[str, Any] = Field(default_factory=dict)
    interview_schedule: dict[str, Any] = Field(default_factory=dict)
    interviewer_names: list[str] = Field(default_factory=list)
    candidate_name: str = ""
    candidate_email: str = ""


# ---------------------------------------------------------------------------
# SimFrame envelope - one frame off the simulator->dashboard->engine wire.
# Dashboard forwards every frame verbatim as `{kind, payload}` JSON over
# the WS connection (see session-client.tsx: `engineSocketRef.current?.send(frame)`).
# ---------------------------------------------------------------------------


class ContextFrame(BaseModel):
    kind: Literal["context"] = "context"
    payload: SessionContext


class SimEventFrame(BaseModel):
    kind: Literal["event"] = "event"
    payload: SimEvent


class StreamFrameEnvelope(BaseModel):
    kind: Literal["stream"] = "stream"
    payload: StreamFrame


class ErrorFrame(BaseModel):
    kind: Literal["error"] = "error"
    payload: Any = None


SimFrame = Annotated[
    Union[ContextFrame, SimEventFrame, StreamFrameEnvelope, ErrorFrame],
    Field(discriminator="kind"),
]


def parse_sim_frame(raw: dict[str, Any]) -> SimFrame:
    """Parse an inbound `{kind, payload}` dict into a typed SimFrame.

    Raises pydantic.ValidationError on malformed input - callers should
    catch this per-message so one bad frame never kills the connection.
    """
    kind = raw.get("kind")
    frame_types: dict[str, type[BaseModel]] = {
        "context": ContextFrame,
        "event": SimEventFrame,
        "stream": StreamFrameEnvelope,
        "error": ErrorFrame,
    }
    model = frame_types.get(kind)
    if model is None:
        raise ValueError(f"unknown SimFrame kind: {kind!r}")
    return model.model_validate(raw)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Evidence - what an Identifier produces. Internal to the engine (never
# sent on the wire directly), but participant.evidence_log entries get
# surfaced in the outbound EngineMessage's `reasoning` / candidate-level
# evidence trail for explainability.
# ---------------------------------------------------------------------------

EvidenceDirection = Literal["for_candidate", "against_candidate"]


class Evidence(BaseModel):
    """Raw output of a single Identifier for a single participant."""

    identifier_id: str
    participant_id: Optional[str]
    signal: str  # short machine-readable signal name, e.g. "name_match"
    direction: EvidenceDirection
    # 0..1, identifier's own confidence in *this particular observation*
    # (not yet weighted by the identifier's configured global weight -
    # that happens in the Evidence Normalizer).
    strength: float
    reasoning: str
    t: float


class NormalizedEvidence(BaseModel):
    """Evidence after the Evidence Normalizer has applied the
    identifier's configured weight and converted it into log-odds deltas
    the Belief Engine can directly accumulate."""

    evidence: Evidence
    identifier_weight: float
    delta_candidate_logit: float
    delta_not_candidate_logit: float


# ---------------------------------------------------------------------------
# Engine -> Dashboard outbound prediction message.
# Matches apps/web/src/lib/types.ts `engineMessageSchema` exactly on the
# required fields; `.passthrough()` on the TS side means we're free to
# add extra fields (probability_not_candidate, evidence, display_name)
# without breaking the dashboard's parser.
# ---------------------------------------------------------------------------


class EngineCandidateOut(BaseModel):
    participant_id: str
    display_name: Optional[str] = None
    confidence: float
    probability_not_candidate: float = 0.0
    reasoning: Optional[str] = None
    evidence: list[str] = Field(default_factory=list)


class EngineMessage(BaseModel):
    type: str = "prediction"
    t: float
    candidate_participant_id: Optional[str] = None
    confidence: Optional[float] = None
    reasoning: Optional[str] = None
    top_candidates: list[EngineCandidateOut] = Field(default_factory=list)
