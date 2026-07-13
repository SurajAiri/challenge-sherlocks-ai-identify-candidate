/**
 * Wire-format types shared across the dashboard.
 *
 * These mirror `apps/simulator/src/simulator/models.py` + `emitter.py`
 * exactly. The Engine should not be able to tell whether events came
 * from the simulator or a real meeting - and neither should this
 * dashboard care, beyond parsing the same three SSE frame kinds.
 *
 * Kept loose on purpose in a couple of places (`data: z.record(...)`,
 * the Engine message schema) because those payloads are intentionally
 * open-ended - authors can put arbitrary fields on authored events,
 * and the Engine doesn't exist yet, so we don't know its exact shape.
 * `.passthrough()` + defensive field lookups let the UI stay useful
 * once it does.
 */
import { z } from "zod";

// ---------------------------------------------------------------------------
// Event / stream primitives (apps/simulator/src/simulator/models.py)
// ---------------------------------------------------------------------------

export const EVENT_TYPES = [
  "participant_join",
  "participant_leave",
  "participant_update",
  "webcam_on",
  "webcam_off",
  "screenshare_start",
  "screenshare_end",
  "speaking_start",
  "speaking_end",
  "transcript_segment",
  "audio_stream_on",
  "audio_stream_off",
] as const;

export type SimEventType = (typeof EVENT_TYPES)[number];

export const simEventSchema = z.object({
  t: z.number(),
  type: z.enum(EVENT_TYPES),
  participant_id: z.string().nullable(),
  data: z.record(z.string(), z.unknown()).default({}),
});
export type SimEvent = z.infer<typeof simEventSchema>;

export const MODALITIES = ["audio", "video", "screenshare"] as const;
export type Modality = (typeof MODALITIES)[number];

export const streamFrameSchema = z.object({
  t: z.number(),
  participant_id: z.string(),
  modality: z.enum(MODALITIES),
  // Globally-unique id for the on..off window this chunk belongs to.
  // `seq` below is only 0-based *within* that window and resets to 0
  // every time the same participant_id+modality opens a new window
  // (e.g. speaking a second time) - so (participant_id, modality, seq)
  // is NOT a safe key on its own once you're doing anything other than
  // "append into whichever window is currently open." Use track_id for
  // storage/dedup/replay. Also present on the matching webcam_on/
  // audio_stream_on/screenshare_start SimEvent's `data.track_id` (and
  // echoed on the matching off event), so a chunk can be tied back to
  // its lifecycle event too.
  track_id: z.string(),
  seq: z.number(),
  data: z.string(), // base64-encoded chunk bytes
});
export type StreamFrame = z.infer<typeof streamFrameSchema>;

export const sessionContextSchema = z.object({
  calendar_invite: z.record(z.string(), z.unknown()).default({}),
  interview_schedule: z.record(z.string(), z.unknown()).default({}),
  interviewer_names: z.array(z.string()).default([]),
  candidate_name: z.string(),
  candidate_email: z.string(),
});
export type SessionContext = z.infer<typeof sessionContextSchema>;

/** One frame off the simulator's `/run` SSE wire (event: <kind>). */
export type SimFrame =
  | { kind: "context"; payload: SessionContext }
  | { kind: "event"; payload: SimEvent }
  | { kind: "stream"; payload: StreamFrame }
  | { kind: "error"; payload: unknown };

// ---------------------------------------------------------------------------
// Simulator API request/response (apps/simulator/src/simulator/api.py)
// ---------------------------------------------------------------------------

export const validateResponseSchema = z.object({
  valid: z.boolean(),
  name: z.string(),
  slug: z.string(),
  participants: z.array(z.string()),
  timeline_events: z.number(),
});
export type ValidateResponse = z.infer<typeof validateResponseSchema>;

export const evaluationResponseSchema = z.object({
  name: z.string(),
  slug: z.string(),
  description: z.string().nullable().optional(),
  ground_truth_participant_id: z.string().nullable().optional(),
  difficulty: z.number().nullable().optional(),
  challenging_points: z.array(z.string()).default([]),
  expected_evidence: z.record(z.string(), z.array(z.string())).default({}),
});
export type EvaluationResponse = z.infer<typeof evaluationResponseSchema>;

// ---------------------------------------------------------------------------
// Engine -> Dashboard prediction messages
//
// The Engine doesn't exist yet, so this is a best-effort contract, not a
// confirmed one. Kept permissive (`.passthrough()`) and read defensively
// (see `lib/engine-client.ts`) so the panel degrades to "raw message" mode
// instead of crashing if the real shape drifts from this guess.
// ---------------------------------------------------------------------------

export const engineCandidateSchema = z
  .object({
    participant_id: z.string(),
    confidence: z.number(),
    reasoning: z.string().optional(),
  })
  .passthrough();

export const engineMessageSchema = z
  .object({
    type: z.string().optional(),
    t: z.number().optional(),
    candidate_participant_id: z.string().nullable().optional(),
    confidence: z.number().nullable().optional(),
    reasoning: z.string().nullable().optional(),
    top_candidates: z.array(engineCandidateSchema).optional(),
  })
  .passthrough();
export type EngineMessage = z.infer<typeof engineMessageSchema>;

// ---------------------------------------------------------------------------
// Scenario library (client-side, persisted)
// ---------------------------------------------------------------------------

export const scenarioLibraryEntrySchema = z.object({
  id: z.string(),
  path: z.string(),
  name: z.string(),
  slug: z.string(),
  description: z.string().nullable().optional(),
  difficulty: z.number().nullable().optional(),
  challengingPoints: z.array(z.string()).default([]),
  expectedEvidence: z.record(z.string(), z.array(z.string())).default({}),
  groundTruthParticipantId: z.string().nullable().optional(),
  addedAt: z.number(),
});
export type ScenarioLibraryEntry = z.infer<typeof scenarioLibraryEntrySchema>;
