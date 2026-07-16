"""
Participant State Repository (State Store).

Single shared source of truth for one interview session: who's in the
call, their presence/media state, accumulated speaking/transcript
stats, and current belief (candidate log-odds). Identifiers get a
read-only view (`ParticipantStateReadOnlyView`); only the Belief Engine
writes belief fields, only `apply_event`/`apply_stream_frame` (called
from the session orchestrator as frames arrive) write presence/media
fields. This split is what the diagram's "Read Only" vs "read
state"/"write state" arrows are encoding.

Notes carried over from the architecture doc:
  - We store TWO probabilities per participant: probability of BEING
    the candidate (the actual answer we need) and probability of NOT
    being the candidate (used to eliminate participants and shrink the
    focus space - these are tracked as independent log-odds, not
    forced to sum to 1 with the positive probability, since "clearly
    an interviewer" and "not yet clearly the candidate" are different
    claims).
  - Identifiers can be instant (fire-and-done off a single event) or
    temporal (reason about a window of time), and independently
    one_time (run once, at participant creation) or continuous (run
    repeatedly as new events arrive). Both axes are represented on the
    Identifier itself (see core/identifiers/base.py), not here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from engine.core.schemas import Evidence, SessionContext, SimEvent, SimEventType, StreamFrame

# How many recent evidence entries to keep per participant for
# explainability. Unbounded growth here would slowly bloat memory over
# a long-running session and nobody needs evidence #1 through #400 in
# the "why did you pick this person" output - the tail is what matters.
MAX_EVIDENCE_LOG = 25
MAX_TRANSCRIPT_SEGMENTS = 200


@dataclass
class TranscriptSegment:
    t: float
    text: str


@dataclass
class IdentifierContribution:
    """One identifier's own running sub-total toward a participant's
    belief - the Belief Engine keeps one of these per identifier that
    has ever emitted Evidence for this participant, instead of one
    shared flat accumulator, specifically so each identifier's
    contribution can decay independently (see core/belief_engine.py).

    `candidate_logit`/`not_candidate_logit` are RAW (undecayed) running
    sums, clamped the same way the old single accumulator was so one
    identifier firing relentlessly can't dominate purely by
    out-accumulating everyone else. Decay is applied lazily, on read,
    by the Belief Engine - `last_touched_t` plus this identifier's own
    `decay_half_life` (carried along here since it's known at the
    moment evidence lands, so recompute doesn't need a registry
    lookup) is all that's needed to compute "how much is this bucket
    worth right now," so nothing has to walk every participant
    "ticking down" a number on a timer.
    """

    candidate_logit: float = 0.0
    not_candidate_logit: float = 0.0
    last_touched_t: float = 0.0
    # None = no decay (sticky, permanent contribution) - the default,
    # and the exact behavior every identifier had before decay existed.
    decay_half_life: Optional[float] = None


@dataclass
class StreamModalityStats:
    """Lightweight presence/liveness stats per modality. Deliberately
    does NOT retain decoded media - that's an identifier's job if/when
    one needs it, not the state store's."""

    is_open: bool = False
    track_id: Optional[str] = None
    frame_count: int = 0
    total_bytes_b64: int = 0
    last_seen_t: Optional[float] = None
    opened_at: Optional[float] = None


@dataclass
class ParticipantState:
    participant_id: str
    display_name: str = ""
    display_name_history: list[tuple[float, str]] = field(default_factory=list)

    is_present: bool = False
    joined_at: Optional[float] = None
    left_at: Optional[float] = None

    webcam_on: bool = False
    screenshare_on: bool = False

    speaking_now: bool = False
    speaking_started_at: Optional[float] = None
    total_speaking_seconds: float = 0.0
    speaking_turns: int = 0

    transcript_segments: list[TranscriptSegment] = field(default_factory=list)
    total_transcript_chars: int = 0
    total_transcript_words: int = 0
    questions_asked: int = 0  # naive heuristic count, see identifiers/qa_pattern.py

    stream_stats: dict[str, StreamModalityStats] = field(
        default_factory=lambda: {
            "audio": StreamModalityStats(),
            "video": StreamModalityStats(),
            "screenshare": StreamModalityStats(),
        }
    )

    # --- belief (owned by the Belief Engine; identifiers must not write these) ---
    # Source of truth: one raw, undecayed sub-total per identifier that
    # has contributed evidence for this participant (see
    # IdentifierContribution above).
    identifier_contributions: dict[str, IdentifierContribution] = field(default_factory=dict)
    # Cached, derived from identifier_contributions as of the last
    # recompute (decayed sum, clamped) - read these for "what does the
    # engine currently believe," don't read identifier_contributions
    # directly unless you specifically need per-identifier detail.
    logit_candidate: float = 0.0
    logit_not_candidate: float = 0.0
    probability_candidate: float = 0.0
    probability_not_candidate: float = 0.0
    evidence_log: list[Evidence] = field(default_factory=list)

    created_at: float = field(default_factory=time.monotonic)
    last_updated_t: float = 0.0

    def record_evidence(self, evidence: Evidence) -> None:
        self.evidence_log.append(evidence)
        if len(self.evidence_log) > MAX_EVIDENCE_LOG:
            self.evidence_log = self.evidence_log[-MAX_EVIDENCE_LOG:]


class ParticipantStateReadOnlyView:
    """What Identifiers see. Wraps a repository and only exposes reads.

    This is a discipline aid, not a security boundary - Python can't
    truly stop a misbehaving identifier from mutating a returned
    dataclass in place. The contract is enforced by convention (and by
    code review / this docstring), same as the diagram's dashed "Read
    Only" arrow is a design intent, not a runtime guarantee.
    """

    def __init__(self, repository: "ParticipantStateRepository") -> None:
        self._repo = repository

    def get(self, participant_id: str) -> Optional[ParticipantState]:
        return self._repo.participants.get(participant_id)

    def all(self) -> list[ParticipantState]:
        return list(self._repo.participants.values())

    def present(self) -> list[ParticipantState]:
        return [p for p in self._repo.participants.values() if p.is_present]

    @property
    def session_context(self) -> Optional[SessionContext]:
        return self._repo.session_context

    @property
    def current_t(self) -> float:
        return self._repo.current_t


class ParticipantStateRepository:
    def __init__(self) -> None:
        self.participants: dict[str, ParticipantState] = {}
        self.session_context: Optional[SessionContext] = None
        self.current_t: float = 0.0

    def read_only_view(self) -> ParticipantStateReadOnlyView:
        return ParticipantStateReadOnlyView(self)

    def set_context(self, context: SessionContext) -> None:
        self.session_context = context

    def get_or_create(self, participant_id: str, t: float) -> tuple[ParticipantState, bool]:
        """Returns (state, is_new). `is_new=True` is the trigger for
        the "Initial One Time Run" identifiers upstream."""
        existing = self.participants.get(participant_id)
        if existing is not None:
            return existing, False
        state = ParticipantState(participant_id=participant_id, created_at=t)
        self.participants[participant_id] = state
        return state, True

    # -- ingestion -----------------------------------------------------

    def apply_event(self, event: SimEvent) -> tuple[Optional[ParticipantState], bool]:
        """Applies a SimEvent's presence/media/transcript side effects.
        Returns (participant_state, is_new_participant). Belief fields
        are never touched here - only the Belief Engine writes those.
        """
        self.current_t = max(self.current_t, event.t)
        if event.participant_id is None:
            # Session-level events (none currently authored, but the
            # wire format allows it) - nothing to attach state to.
            return None, False

        state, is_new = self.get_or_create(event.participant_id, event.t)
        state.last_updated_t = event.t

        match event.type:
            case SimEventType.PARTICIPANT_JOIN:
                state.is_present = True
                state.joined_at = event.t
                name = event.data.get("display_name")
                if isinstance(name, str) and name:
                    self._set_display_name(state, name, event.t)
            case SimEventType.PARTICIPANT_LEAVE:
                state.is_present = False
                state.left_at = event.t
            case SimEventType.PARTICIPANT_UPDATE:
                name = event.data.get("display_name")
                if isinstance(name, str) and name and name != state.display_name:
                    self._set_display_name(state, name, event.t)
            case SimEventType.WEBCAM_ON:
                state.webcam_on = True
            case SimEventType.WEBCAM_OFF:
                state.webcam_on = False
            case SimEventType.SCREENSHARE_START:
                state.screenshare_on = True
            case SimEventType.SCREENSHARE_END:
                state.screenshare_on = False
            case SimEventType.SPEAKING_START:
                state.speaking_now = True
                state.speaking_started_at = event.t
                state.speaking_turns += 1
            case SimEventType.SPEAKING_END:
                if state.speaking_started_at is not None:
                    state.total_speaking_seconds += max(0.0, event.t - state.speaking_started_at)
                state.speaking_now = False
                state.speaking_started_at = None
            case SimEventType.TRANSCRIPT_SEGMENT:
                text = event.data.get("text")
                if isinstance(text, str) and text:
                    state.transcript_segments.append(TranscriptSegment(t=event.t, text=text))
                    if len(state.transcript_segments) > MAX_TRANSCRIPT_SEGMENTS:
                        state.transcript_segments = state.transcript_segments[-MAX_TRANSCRIPT_SEGMENTS:]
                    state.total_transcript_chars += len(text)
                    state.total_transcript_words += len(text.split())
            case SimEventType.AUDIO_STREAM_ON:
                stats = state.stream_stats["audio"]
                stats.is_open = True
                stats.opened_at = event.t
                stats.track_id = event.data.get("track_id")
            case SimEventType.AUDIO_STREAM_OFF:
                state.stream_stats["audio"].is_open = False

        return state, is_new

    def apply_stream_frame(self, frame: StreamFrame) -> ParticipantState:
        self.current_t = max(self.current_t, frame.t)
        state, _ = self.get_or_create(frame.participant_id, frame.t)
        stats = state.stream_stats.setdefault(frame.modality, StreamModalityStats())
        stats.is_open = True
        stats.track_id = frame.track_id
        stats.frame_count += 1
        # Cheap proxy for "how much media" without decoding base64 -
        # useful as a liveness/activity signal, nothing more.
        stats.total_bytes_b64 += len(frame.data)
        stats.last_seen_t = frame.t
        state.last_updated_t = frame.t
        return state

    def _set_display_name(self, state: ParticipantState, name: str, t: float) -> None:
        if state.display_name:
            state.display_name_history.append((t, state.display_name))
        state.display_name = name

    # -- helpers ---------------------------------------------------------

    def total_speaking_seconds_all(self) -> float:
        return sum(p.total_speaking_seconds for p in self.participants.values())
