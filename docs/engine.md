# Engine Internals

> **Audience:** Engineers extending or debugging the Belief Engine.
> For a high-level system overview, see the [README](../README.md).

---

## Table of Contents

1. [Data Flow](#data-flow)
2. [State Store](#state-store)
3. [Identifiers](#identifiers)
4. [Belief Engine](#belief-engine)
5. [Detection State Machine](#detection-state-machine)
6. [Output Formatter](#output-formatter)
7. [Tuning Constants Quick Reference](#tuning-constants-quick-reference)

---

## Data Flow

```
Incoming event / stream frame
         │
         ▼
  SessionEngine.handle_event()
         │
         ├─► apply_event() → ParticipantStateRepository (presence / media fields)
         │
         └─► foreach Identifier.run()
                   │
                   ▼
           NormalizedEvidence
                   │
                   ▼
          BeliefEngine.apply()
                   │
                   ├─► identifier_contributions updated (decayed log-odds per source)
                   └─► recompute_probabilities()
                             │
                             ├─► softmax(candidate_logits) → probability_candidate
                             ├─► sigmoid(logit_not_candidate) → probability_not_candidate
                             └─► DetectionStateTracker.update()
                                           │
                                           ▼
                                  DetectionState (EXPLORING → SEARCHING
                                                → LIKELY_CANDIDATE
                                                → STABLE_CANDIDATE)
                                           │
                                           ▼
                                 OutputFormatter.format_message()
                                           │
                                           ▼
                                    EngineMessage (WebSocket →  Dashboard)
```

---

## State Store

**File:** `core/state_store.py`

`ParticipantStateRepository` is the single shared source of truth for one session. It owns:

- `participants: dict[str, ParticipantState]` — one entry per participant ID
- `current_t: float` — simulation clock (seconds), advanced on every event
- `session_context: SessionContext` — calendar metadata (candidate name, interviewers)

`ParticipantState` fields relevant to belief:

| Field | Writer | Purpose |
|---|---|---|
| `identifier_contributions` | `BeliefEngine` only | Decayed log-odds bucket per identifier |
| `logit_candidate` | `BeliefEngine` only | Clamped total candidate log-odds |
| `logit_not_candidate` | `BeliefEngine` only | Clamped total not-candidate log-odds |
| `probability_candidate` | `BeliefEngine` only | Post-softmax output |
| `probability_not_candidate` | `BeliefEngine` only | Post-sigmoid output |
| `evidence_log` | `BeliefEngine` only | Last N reasoning strings (for explainability) |

Only `BeliefEngine` writes belief fields. Everything else reads via `ParticipantStateReadOnlyView`.

---

## Identifiers

**Directory:** `identifiers/`

Each identifier is a Python class with a `run()` method. It receives a read-only view of the state store and the current event/frame, and returns zero or more `NormalizedEvidence` objects.

### Identifier taxonomy

| Axis | Values | Meaning |
|---|---|---|
| **Timing** | `instant` / `temporal` | Fires on a single event vs. reasons over a time window |
| **Run count** | `one_time` / `continuous` | Runs once per participant join vs. on every relevant event |

### Available identifiers

| File | Signal |
|---|---|
| `name_match.py` | Fuzzy match of display name vs. `context.candidate_name` |
| `email_identity.py` | Email match from participant metadata |
| `host_organizer.py` | Meeting host / calendar organiser flag |
| `speaking_share.py` | Speaking-time ratio vs. equal split across participants |
| `qa_pattern.py` | Asymmetric Q&A — one person consistently answers questions |
| `screenshare_heuristic.py` | Screen-share toggle when the interviewer asks |
| `silent_observer.py` | Long silence in a multi-participant call → less likely candidate |
| `llm_name_role.py` | LLM extracts name / role from transcript turns |
| `llm_transcript_role.py` | LLM classifies conversational role (interviewer vs. interviewee) |

### Adding a new identifier

1. Create `identifiers/my_signal.py` subclassing `BaseIdentifier`.
2. Implement `run(state_view, event_or_frame) -> list[NormalizedEvidence]`.
3. Register it in `identifiers/__init__.py`.

The identifier should never write to the state store — only return evidence objects. The `BeliefEngine` applies them.

---

## Belief Engine

**File:** `core/belief_engine.py`

### Two independent probability tracks

**`probability_candidate`** is derived from a **softmax** across all participants' `logit_candidate` values. This is the primary output — it is inherently a competition. As one participant accumulates strong signal, the others' probabilities drop proportionally.

**`probability_not_candidate`** is derived from a **sigmoid** applied independently per participant. Multiple participants can simultaneously sit at 0.9+ not-candidate. This track is used for elimination (skip expensive identifiers for eliminated participants) and is reported separately in the engine message.

### Per-identifier decay

Each identifier's contribution is stored as a separate `IdentifierContribution` bucket — not a single aggregated float. On every `recompute_probabilities()` call, each bucket's logit is multiplied by an exponential decay factor:

```
factor = exp(-ln(2) * elapsed / decay_half_life)
```

where `elapsed = current_t - contribution.last_touched_t`. This means old evidence fades naturally — useful for identifiers where stale signal is misleading (e.g. speaking-share measured in the first 30 seconds may not represent the full call).

### No-evidence baseline

Participants who have not yet received any identifier evidence are fed into the softmax with `NO_EVIDENCE_BASELINE_LOGIT = -1.5` rather than `0.0`. Without this, a single participant in an otherwise-empty pool gets `softmax({pid: 0.0}) = 1.0` — 100% confidence from nothing. The baseline keeps no-evidence participants well below any reporting threshold.

---

## Detection State Machine

**File:** `core/detection_state.py`

The state machine is a *read* of the belief, not a new scoring system. It reuses the same threshold constants as the output formatter so "what state are we in" and "what did we tell the client" can never silently disagree.

```
                     ┌──────────────┐
                     │   EXPLORING  │  (initial state, warmup gate)
                     └──────┬───────┘
                            │  elapsed_t ≥ MIN_ELAPSED_SECONDS
                            │  AND total evidence ≥ MIN_EVIDENCE_PIECES
                            ▼
                     ┌──────────────┐
                     │   SEARCHING  │
                     └──────┬───────┘
                            │  top.probability_candidate ≥ INSUFFICIENT_EVIDENCE_THRESHOLD
                            ▼
                     ┌──────────────────┐
                     │ LIKELY_CANDIDATE │
                     └──────┬───────────┘
                            │  top ≥ CONFIDENT_THRESHOLD AND margin > AMBIGUITY_MARGIN
                            │  held for STABLE_ENTRY_STREAK snapshots
                            ▼
                     ┌──────────────────┐
              ┌─────▶│ STABLE_CANDIDATE │◀─────────────────────────┐
              │      └──────┬───────────┘                          │
              │             │  top < STABLE_EXIT_THRESHOLD          │
              │             │  OR margin ≤ AMBIGUITY_MARGIN         │
              │             ▼                                       │
              │      ┌──────────────────┐                          │
              │      │  LOST_CANDIDATE  │ (transitional, one step) │
              │      └──────────────────┘                          │
              │             │  re-derive fresh on next snapshot      │
              └─────────────┘──────────────────────────────────────┘
```

### Warmup gate (EXPLORING)

The `EXPLORING` state is the engine's commitment to not rush. It remains in `EXPLORING` until **both** of the following are true:

- **`elapsed_t >= MIN_ELAPSED_SECONDS`** (default: 20 s) — minimum session time on the simulation clock.
- **`total evidence >= MIN_EVIDENCE_PIECES`** (default: 3) — total evidence log entries summed across all participants.

The gate is **one-way**: once the engine leaves `EXPLORING` it never re-enters (lost-signal scenarios use `LOST_CANDIDATE`).

During `EXPLORING`, `output_formatter.py` returns `possible_candidate_ids = []` unconditionally, so no premature candidate ever reaches the downstream system or dashboard — even if the softmax math happens to produce a high number for one participant due to early noise.

### Hysteresis

`STABLE_CANDIDATE` has separate entry and exit thresholds (`CONFIDENT_THRESHOLD` vs. `STABLE_EXIT_THRESHOLD`) plus a streak requirement (`STABLE_ENTRY_STREAK` consecutive qualifying snapshots). This prevents the engine from flickering in and out of "stable" for participants sitting right at the threshold boundary.

---

## Output Formatter

**File:** `core/output_formatter.py`

Converts the current repository snapshot into an `EngineMessage` that flows to the dashboard over WebSocket. The key field is `possible_candidate_ids`:

| Value | Meaning |
|---|---|
| `[]` | During `EXPLORING`: warmup not cleared. During `SEARCHING`: nobody above the evidence floor. |
| `[id]` | One participant is a sustained, unambiguous leader (`STABLE_CANDIDATE`). |
| `[id, id, ...]` | Multiple participants are within `AMBIGUITY_MARGIN` of the leader, or the leader hasn't yet proven durable. |

The `evidence` field is a `dict[participant_id → list[reasoning_string]]` — only populated for IDs present in `possible_candidate_ids`, so the UI shows explanations only for the participants actively being considered.

---

## Tuning Constants Quick Reference

| Constant | File | Default | What to change it for |
|---|---|---|---|
| `MIN_ELAPSED_SECONDS` | `detection_state.py` | `20.0` | Lengthen warmup if identifiers need more ramp time |
| `MIN_EVIDENCE_PIECES` | `detection_state.py` | `3` | Raise if early evidence is noisy / sparse |
| `NO_EVIDENCE_BASELINE_LOGIT` | `belief_engine.py` | `-1.5` | Lower to further suppress zero-evidence participants |
| `INSUFFICIENT_EVIDENCE_THRESHOLD` | `output_formatter.py` | `0.35` | Raise to be more conservative before mentioning anyone |
| `CONFIDENT_THRESHOLD` | `output_formatter.py` | `0.55` | Raise for stricter single-candidate collapse |
| `AMBIGUITY_MARGIN` | `output_formatter.py` | `0.15` | Raise to require a larger gap before collapsing to one id |
| `STABLE_ENTRY_STREAK` | `detection_state.py` | `2` | Increase to require a longer sustained lead |
| `STABLE_EXIT_THRESHOLD` | `detection_state.py` | `0.45` | Widen the hysteresis band if state flaps at a boundary |
| `LOGIT_CLAMP` | `belief_engine.py` | `10.0` | Maximum absolute logit value (prevents runaway accumulation) |
