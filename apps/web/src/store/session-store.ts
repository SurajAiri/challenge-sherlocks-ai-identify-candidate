"use client";

import { create } from "zustand";
import { base64ToBytes, concatBytes, pcmToWavBlob } from "@/lib/audio";
import { makeId } from "@/lib/id";
import type { SessionContext, SimEvent, SimFrame, StreamFrame } from "@/lib/types";
import type { EngineStatus } from "@/lib/engine-client";

const MAX_LOG_ENTRIES = 500;
const MAX_TRANSCRIPT_ENTRIES = 500;

export type RunStatus = "idle" | "connecting" | "streaming" | "completed" | "error";

export interface AudioTrackBuffer {
  active: boolean;
  trackId: string;
  sampleRate: number;
  channels: number;
  bitsPerSample: number;
  chunks: Uint8Array[];
  startedAt: number;
}

export interface ParticipantState {
  participantId: string;
  displayName: string;
  joined: boolean;
  joinedAt: number | null;
  leftAt: number | null;
  nameHistory: { name: string; t: number }[];
  webcamOn: boolean;
  webcamMeta: { width?: number; height?: number; fps?: number } | null;
  lastFrameDataUrl: string | null;
  screenshareOn: boolean;
  screenshareMeta: { width?: number; height?: number; fps?: number } | null;
  lastScreenshareFrameDataUrl: string | null;
  speaking: boolean;
  micOn: boolean;
  audioTrack: AudioTrackBuffer | null;
  speakingSeconds: number;
  segmentCount: number;
}

export interface TranscriptSegment {
  id: string;
  t: number;
  participantId: string | null;
  displayName: string;
  text: string;
  // The audio on/off window this segment was spoken during, per the
  // compiler's own `data.audio_track_id` stamp (see compiler.py). This
  // is the *only* reliable way to join a segment back to its audio -
  // arrival order between the two is NOT reliable (audio_stream_off is
  // auto-derived and emitted before the authored transcript_segment
  // that follows it - see applyEvent below). null when this segment
  // has no matching audio window at all (e.g. text-only fixtures).
  audioTrackId: string | null;
  audioBlobUrl: string | null;
  audioPending: boolean;
}

export interface RawLogEntry {
  id: string;
  t: number;
  kind: "context" | "event" | "stream" | "error";
  summary: string;
  detail?: unknown;
}

export interface EnginePrediction {
  id: string;
  t: number;
  receivedAt: number;
  candidateParticipantId: string | null;
  confidence: number | null;
  reasoning: string | null;
  raw: unknown;
}

interface SessionState {
  scenarioLibraryId: string | null;
  scenarioPath: string | null;
  scenarioName: string | null;

  runStatus: RunStatus;
  runError: string | null;
  runStartedAt: number | null;
  runAbort: AbortController | null;

  context: SessionContext | null;
  participants: Record<string, ParticipantState>;
  participantOrder: string[];

  transcript: TranscriptSegment[];
  rawLog: RawLogEntry[];

  // Finished WAV blob URLs keyed by track_id, for audio windows whose
  // audio_stream_off has already resolved before the matching
  // transcript_segment event (the common case - see the comment on
  // TranscriptSegment.audioTrackId). Consumed (and deleted) as soon as
  // the matching segment arrives; anything left behind at the end of a
  // run means a track_id never got a transcript_segment at all, which
  // is fine and expected for some scenarios.
  pendingAudioByTrackId: Record<string, string>;

  // Decoding chunks into a playable WAV blob happens unconditionally now
  // (see audio_stream_off below) - there is no user-facing toggle for it
  // any more. It's cheap (one Blob per utterance) and every other piece
  // of audio UX (the per-segment Play button below) is useless without
  // it, so gating it behind a switch only ever produced "why is there no
  // audio" confusion for zero real benefit.

  // Raw PCM chunks awaiting real-time playback, in arrival order.
  // Appended to in applyStreamFrame the instant a chunk for a currently-
  // open, live-playback-eligible track arrives - NOT batched per
  // utterance. <LiveAudioPlayer/> drains this every time it changes and
  // schedules each chunk back-to-back on a Web Audio graph, so audio
  // starts within one chunk (~audio_chunk_ms) of the mic opening, not
  // one whole utterance later. Only ever appended to when
  // livePlaybackEnabled is true at arrival time.
  liveAudioChunkQueue: LiveAudioChunk[];

  // User toggle for the queue above. Deliberately independent of whether
  // audio gets decoded for the manual Play button (that's unconditional)
  // - this only controls whether decoded audio gets auto-played live.
  // The UI forces this back to false (and disables the switch) whenever
  // runSpeedMultiplier is unset or > 8. Reason: <LiveAudioPlayer/>
  // compensates for sim speed by playing back at `playbackRate =
  // runSpeedMultiplier` (see that file) so a sped-up run stays roughly
  // in sync instead of endlessly falling behind - but playbackRate
  // pushes pitch up 1:1 with it, and beyond ~8x the result is an
  // unintelligible chipmunk squeal rather than "sped up speech."
  livePlaybackEnabled: boolean;

  // Overrides the scenario's authored controls.speed_multiplier for the
  // *next* run started via handleStart -> startSimulatorRun. Only takes
  // effect at run-start time (passed once in the /run request body) -
  // changing it mid-stream does nothing to an already-running session,
  // since the simulator's own clock is what's actually being sped up,
  // not anything client-side. null = use whatever index.yml authored.
  runSpeedMultiplier: number | null;

  engineStatus: EngineStatus;
  engineLatest: EnginePrediction | null;
  engineHistory: EnginePrediction[];

  // actions
  startSession: (scenario: { libraryId: string; path: string; name: string }) => void;
  setRunStatus: (status: RunStatus, error?: string | null) => void;
  setRunAbort: (controller: AbortController | null) => void;
  handleSimFrame: (frame: SimFrame) => void;
  handleEngineMessage: (raw: unknown) => void;
  setEngineStatus: (status: EngineStatus) => void;
  setLivePlaybackEnabled: (enabled: boolean) => void;
  dequeueLiveAudioChunks: (count: number) => void;
  setRunSpeedMultiplier: (speed: number | null) => void;
}

export interface LiveAudioChunk {
  trackId: string;
  bytes: Uint8Array;
  sampleRate: number;
  channels: number;
}

function newParticipant(participantId: string, displayName: string): ParticipantState {
  return {
    participantId,
    displayName,
    joined: false,
    joinedAt: null,
    leftAt: null,
    nameHistory: [{ name: displayName, t: 0 }],
    webcamOn: false,
    webcamMeta: null,
    lastFrameDataUrl: null,
    screenshareOn: false,
    screenshareMeta: null,
    lastScreenshareFrameDataUrl: null,
    speaking: false,
    micOn: false,
    audioTrack: null,
    speakingSeconds: 0,
    segmentCount: 0,
  };
}

function pushCapped<T>(list: T[], item: T, max: number): T[] {
  const next = [...list, item];
  return next.length > max ? next.slice(next.length - max) : next;
}

function approxBytes(base64Length: number): number {
  return Math.floor((base64Length * 3) / 4);
}

export const useSessionStore = create<SessionState>()((set, get) => ({
  scenarioLibraryId: null,
  scenarioPath: null,
  scenarioName: null,

  runStatus: "idle",
  runError: null,
  runStartedAt: null,
  runAbort: null,

  context: null,
  participants: {},
  participantOrder: [],

  transcript: [],
  rawLog: [],
  pendingAudioByTrackId: {},

  liveAudioChunkQueue: [],
  livePlaybackEnabled: false,
  runSpeedMultiplier: null,

  engineStatus: "idle",
  engineLatest: null,
  engineHistory: [],

  startSession: ({ libraryId, path, name }) => {
    const prev = get();
    prev.runAbort?.abort();
    // Revoke every blob URL from a previous run before dropping them -
    // otherwise repeated "Try again" runs on the same scenario leak one
    // Blob per spoken utterance for the lifetime of the tab.
    for (const seg of prev.transcript) {
      if (seg.audioBlobUrl) URL.revokeObjectURL(seg.audioBlobUrl);
    }
    for (const url of Object.values(prev.pendingAudioByTrackId)) {
      URL.revokeObjectURL(url);
    }
    set({
      scenarioLibraryId: libraryId,
      scenarioPath: path,
      scenarioName: name,
      runStatus: "idle",
      runError: null,
      runStartedAt: null,
      runAbort: null,
      context: null,
      participants: {},
      participantOrder: [],
      transcript: [],
      rawLog: [],
      pendingAudioByTrackId: {},
      // Chunks in here haven't been decoded into anything durable (no
      // Blob/objectURL was ever created for them) - safe to just drop,
      // nothing to revoke.
      liveAudioChunkQueue: [],
      engineLatest: null,
      engineHistory: [],
    });
  },

  setRunStatus: (status, error = null) =>
    set({ runStatus: status, runError: error, ...(status === "streaming" ? { runStartedAt: Date.now() } : {}) }),

  setRunAbort: (controller) => set({ runAbort: controller }),

  setEngineStatus: (status) => set({ engineStatus: status }),

  setLivePlaybackEnabled: (enabled) => set({ livePlaybackEnabled: enabled }),
  dequeueLiveAudioChunks: (count) =>
    set((s) => ({ liveAudioChunkQueue: s.liveAudioChunkQueue.slice(count) })),
  setRunSpeedMultiplier: (speed) => set({ runSpeedMultiplier: speed }),

  handleSimFrame: (frame) => {
    if (frame.kind === "context") {
      set({ context: frame.payload });
      return;
    }
    if (frame.kind === "error") {
      const entry: RawLogEntry = {
        id: makeId("log"),
        t: get().rawLog.at(-1)?.t ?? 0,
        kind: "error",
        summary: typeof frame.payload === "string" ? frame.payload : JSON.stringify(frame.payload),
        detail: frame.payload,
      };
      set((s) => ({ rawLog: pushCapped(s.rawLog, entry, MAX_LOG_ENTRIES) }));
      return;
    }
    if (frame.kind === "event") {
      applyEvent(frame.payload, set, get);
      return;
    }
    if (frame.kind === "stream") {
      applyStreamFrame(frame.payload, set, get);
      return;
    }
  },

  handleEngineMessage: (raw) => {
    const prediction = parseEnginePrediction(raw);
    set((s) => ({
      engineLatest: prediction,
      engineHistory: pushCapped(s.engineHistory, prediction, 200),
    }));
  },
}));

function ensureParticipant(
  state: SessionState,
  participantId: string,
  fallbackName?: string
): { participants: Record<string, ParticipantState>; participantOrder: string[] } {
  if (state.participants[participantId]) {
    return { participants: state.participants, participantOrder: state.participantOrder };
  }
  const participant = newParticipant(participantId, fallbackName ?? participantId);
  return {
    participants: { ...state.participants, [participantId]: participant },
    participantOrder: [...state.participantOrder, participantId],
  };
}

function applyEvent(
  event: SimEvent,
  set: (fn: (s: SessionState) => Partial<SessionState>) => void,
  get: () => SessionState
) {
  const pid = event.participant_id;
  const data = event.data ?? {};

  const logEntry: RawLogEntry = {
    id: makeId("log"),
    t: event.t,
    kind: "event",
    summary: describeEvent(event, get()),
    detail: event,
  };
  set((s) => ({ rawLog: pushCapped(s.rawLog, logEntry, MAX_LOG_ENTRIES) }));

  if (!pid) return; // silence/other clock-only markers never reach here, but be safe

  set((state) => {
    const base = ensureParticipant(state, pid, typeof data.display_name === "string" ? data.display_name : undefined);
    const participant = { ...base.participants[pid] };
    let transcript = state.transcript;
    let pendingAudio = state.pendingAudioByTrackId;

    switch (event.type) {
      case "participant_join": {
        participant.joined = true;
        participant.joinedAt = event.t;
        participant.leftAt = null;
        if (typeof data.display_name === "string" && data.display_name !== participant.displayName) {
          participant.displayName = data.display_name;
          participant.nameHistory = [...participant.nameHistory, { name: data.display_name, t: event.t }];
        }
        break;
      }
      case "participant_leave": {
        participant.joined = false;
        participant.leftAt = event.t;
        break;
      }
      case "participant_update": {
        if (typeof data.display_name === "string" && data.display_name !== participant.displayName) {
          participant.displayName = data.display_name;
          participant.nameHistory = [...participant.nameHistory, { name: data.display_name, t: event.t }];
        }
        break;
      }
      case "webcam_on": {
        participant.webcamOn = true;
        participant.webcamMeta = {
          width: numOrUndef(data.width),
          height: numOrUndef(data.height),
          fps: numOrUndef(data.fps),
        };
        break;
      }
      case "webcam_off": {
        participant.webcamOn = false;
        break;
      }
      case "screenshare_start": {
        participant.screenshareOn = true;
        participant.screenshareMeta = {
          width: numOrUndef(data.width),
          height: numOrUndef(data.height),
          fps: numOrUndef(data.fps),
        };
        break;
      }
      case "screenshare_end": {
        participant.screenshareOn = false;
        break;
      }
      case "speaking_start": {
        participant.speaking = true;
        break;
      }
      case "speaking_end": {
        participant.speaking = false;
        break;
      }
      case "audio_stream_on": {
        participant.micOn = true;
        participant.audioTrack = {
          active: true,
          trackId: typeof data.track_id === "string" ? data.track_id : makeId("track"),
          sampleRate: numOrUndef(data.sample_rate) ?? 16000,
          channels: numOrUndef(data.channels) ?? 1,
          bitsPerSample: 16,
          chunks: [],
          startedAt: event.t,
        };
        break;
      }
      case "audio_stream_off": {
        participant.micOn = false;
        const track = participant.audioTrack;
        // Guard against a stray/duplicate off event that doesn't match
        // whatever window is actually open right now.
        const offTrackId = typeof data.track_id === "string" ? data.track_id : null;
        if (track && (!offTrackId || track.trackId === offTrackId) && track.chunks.length) {
          const pcm = concatBytes(track.chunks);
          const blob = pcmToWavBlob(pcm, {
            sampleRate: track.sampleRate,
            channels: track.channels,
            bitsPerSample: track.bitsPerSample,
          });
          const url = URL.createObjectURL(blob);
          // Join to the segment by its explicit audio_track_id (the id
          // the compiler stamped on both sides of this relationship -
          // see compiler.py), NOT by "whichever segment happens to be
          // most recent" - audio_stream_off routinely arrives BEFORE
          // the transcript_segment event for the same window (it's
          // auto-derived and appended to the compiled timeline before
          // the loop even reaches the authored transcript_segment that
          // follows it), so a same-participant/most-recent-pending
          // heuristic here silently attaches this audio to whatever
          // *previous* segment is still waiting - or drops it if none
          // is, which is exactly the "audio shifted onto the wrong
          // card / first one vanishes / solo utterance never resolves"
          // symptom.
          const idx = state.transcript.findIndex((seg) => seg.audioTrackId === track.trackId);
          if (idx !== -1) {
            transcript = state.transcript.map((seg, i) =>
              i === idx ? { ...seg, audioBlobUrl: url, audioPending: false } : seg
            );
          } else {
            // transcript_segment for this window hasn't arrived yet -
            // stash the finished blob so it can be attached the moment
            // that event does show up (this is in fact the normal case
            // given the ordering above).
            pendingAudio = { ...pendingAudio, [track.trackId]: url };
          }
        } else if (track) {
          // Nothing buffered (e.g. zero chunks arrived) or a mismatched
          // off - clear the pending flag on any segment already waiting
          // on this exact track so its Play button doesn't stay
          // "Buffering…" forever.
          transcript = state.transcript.map((seg) =>
            seg.audioTrackId === track.trackId && seg.audioPending ? { ...seg, audioPending: false } : seg
          );
        }
        participant.audioTrack = null;
        break;
      }
      case "transcript_segment": {
        const trackId = typeof data.audio_track_id === "string" ? data.audio_track_id : null;
        const resolvedUrl = trackId ? pendingAudio[trackId] : undefined;
        if (resolvedUrl && trackId) {
          pendingAudio = Object.fromEntries(
            Object.entries(pendingAudio).filter(([id]) => id !== trackId)
          );
        }
        const segment: TranscriptSegment = {
          id: makeId("seg"),
          t: event.t,
          participantId: pid,
          displayName: participant.displayName,
          text: typeof data.text === "string" ? data.text : "",
          audioTrackId: trackId,
          audioBlobUrl: resolvedUrl ?? null,
          // Only "buffering" if there's an actual track to wait on -
          // a segment with no audio_track_id (no matching audio_stream_on
          // at all) has nothing coming, ever.
          audioPending: !!trackId && !resolvedUrl,
        };
        transcript = pushCapped(state.transcript, segment, MAX_TRANSCRIPT_ENTRIES);
        participant.segmentCount += 1;
        break;
      }
    }

    return {
      participants: { ...base.participants, [pid]: participant },
      participantOrder: base.participantOrder,
      transcript,
      pendingAudioByTrackId: pendingAudio,
    };
  });
}

function applyStreamFrame(
  frame: StreamFrame,
  set: (fn: (s: SessionState) => Partial<SessionState>) => void,
  get: () => SessionState
) {
  const state = get();
  const logEntry: RawLogEntry = {
    id: makeId("log"),
    t: frame.t,
    kind: "stream",
    summary: `stream:${frame.modality} [${state.participants[frame.participant_id]?.displayName ?? frame.participant_id}] seq=${frame.seq} (~${approxBytes(frame.data.length)}B)`,
  };
  set((s) => ({ rawLog: pushCapped(s.rawLog, logEntry, MAX_LOG_ENTRIES) }));

  set((state) => {
    const base = ensureParticipant(state, frame.participant_id);
    const participant = { ...base.participants[frame.participant_id] };
    let liveQueue = state.liveAudioChunkQueue;

    if (frame.modality === "video") {
      participant.lastFrameDataUrl = `data:image/jpeg;base64,${frame.data}`;
    } else if (frame.modality === "screenshare") {
      participant.lastScreenshareFrameDataUrl = `data:image/jpeg;base64,${frame.data}`;
    } else if (
      frame.modality === "audio" &&
      participant.audioTrack &&
      participant.audioTrack.trackId === frame.track_id
    ) {
      // trackId guard: without it, a chunk that arrives after its own
      // audio_stream_off (or belonging to a since-superseded window)
      // could get appended into whatever window happens to be open
      // now, corrupting an unrelated utterance's audio.
      const bytes = base64ToBytes(frame.data);
      participant.audioTrack = {
        ...participant.audioTrack,
        chunks: [...participant.audioTrack.chunks, bytes],
      };
      // Live playback is per-chunk, not per-utterance: queuing this the
      // instant it arrives (rather than waiting for audio_stream_off to
      // batch the whole utterance into one blob) is what lets
      // <LiveAudioPlayer/> start sound within ~one audio_chunk_ms of the
      // mic opening instead of a full utterance-length later.
      if (state.livePlaybackEnabled) {
        liveQueue = [
          ...liveQueue,
          {
            trackId: frame.track_id,
            bytes,
            sampleRate: participant.audioTrack.sampleRate,
            channels: participant.audioTrack.channels,
          },
        ];
      }
    }

    return {
      participants: { ...base.participants, [frame.participant_id]: participant },
      participantOrder: base.participantOrder,
      liveAudioChunkQueue: liveQueue,
    };
  });
}

function numOrUndef(v: unknown): number | undefined {
  return typeof v === "number" ? v : undefined;
}

function describeEvent(event: SimEvent, state: SessionState): string {
  const name = event.participant_id ? state.participants[event.participant_id]?.displayName ?? event.participant_id : "";
  const extra = Object.keys(event.data ?? {}).length ? ` ${JSON.stringify(event.data)}` : "";
  return `${event.type}${name ? ` [${name}]` : ""}${extra}`;
}

function parseEnginePrediction(raw: unknown): EnginePrediction {
  const obj = (raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {}) as Record<string, unknown>;

  // Defensive/best-effort field lookup: the Engine's real message shape
  // isn't confirmed yet, so accept a handful of plausible key names
  // rather than assuming one exact contract.
  const candidateParticipantId =
    firstString(obj.candidate_participant_id, obj.candidateParticipantId, obj.participant_id, obj.candidate_id) ??
    null;
  const confidence = firstNumber(obj.confidence, obj.score);
  const reasoning = firstString(obj.reasoning, obj.explanation, obj.rationale) ?? null;
  const t = firstNumber(obj.t, obj.timestamp) ?? 0;

  return {
    id: makeId("pred"),
    t,
    receivedAt: Date.now(),
    candidateParticipantId,
    confidence: confidence ?? null,
    reasoning,
    raw,
  };
}

function firstString(...values: unknown[]): string | undefined {
  for (const v of values) if (typeof v === "string") return v;
  return undefined;
}

function firstNumber(...values: unknown[]): number | undefined {
  for (const v of values) if (typeof v === "number") return v;
  return undefined;
}
