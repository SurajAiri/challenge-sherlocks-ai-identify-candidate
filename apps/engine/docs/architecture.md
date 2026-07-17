# Engine Architecture

This document maps the system's architecture diagram (`arch.png` at the
repo root) to the actual code in `apps/engine/src/engine/`.

## Where a frame comes from

```
Simulator --SSE--> Dashboard --WS--> Engine
```

The dashboard (`apps/web`) is a thin relay: it parses the simulator's SSE
stream into typed frames (`{kind, payload}` where `kind` is one of
`context | event | stream | error`) and forwards every frame verbatim over
a WebSocket to the Engine, unchanged (`session-client.tsx`:
`engineSocketRef.current?.send(frame)`). The Engine never knows or cares
whether a frame originated from the simulator or a real Meet/Zoom/Teams
adapter - that boundary is entirely the dashboard's job.

## Engine internals (`src/engine/`)

```
api/
  api.py            FastAPI app, mounts the ws router + /health
  ws.py             WebSocket endpoint: one connection = one SessionEngine.
                     Parses raw JSON -> typed SimFrame, feeds the engine,
                     streams EngineMessage JSON back down the same socket.
                     Also runs a periodic heartbeat so the dashboard's
                     confidence panel stays fresh through quiet stretches.

core/
  schemas.py         Wire-format contract. Mirrors apps/web/src/lib/types.ts
                      field-for-field: SessionContext, SimEvent, StreamFrame,
                      the SimFrame envelope, plus Evidence/NormalizedEvidence
                      (internal) and EngineMessage (outbound).
  event_bus.py        Generic async pub/sub, reused for two purposes:
                        - raw_bus: SimEvents fan out to identifiers that
                          declared interest in that event type.
                        - evidence_bus: Identifiers publish Evidence; the
                          normalizer/belief pipeline is the subscriber.
  state_store.py       Participant State Repository. Single source of truth
                        for identity (display name + history), presence,
                        media state, speaking/transcript stats, and belief
                        (logit_candidate / logit_not_candidate). Identifiers
                        get a read-only view; only the Belief Engine writes
                        belief fields.
  identifiers/base.py  Identifier ABC. Two independent axes: INSTANT vs
                        TEMPORAL (what it looks at) and ONE_TIME vs
                        CONTINUOUS vs BOTH (when it runs).
  identifiers/registry.py  Pluggable, weighted set of active identifiers.
                            `default_registry()` is the one place to edit
                            when adding/removing an identifier.
  evidence_normalizer.py  Converts an identifier's raw 0..1 Evidence into
                            log-odds deltas, applying the identifier's
                            configured weight. The one place strength x
                            weight -> logit is defined, so identifiers never
                            think about logits and weight tuning never
                            touches identifier code.
  belief_engine.py     Accumulates normalized evidence into per-participant
                        log-odds and produces the probability snapshot:
                          - probability_candidate: softmax across all
                            currently-known participants (competing
                            hypotheses - it's inherently "who, among these
                            people, is most likely the candidate").
                          - probability_not_candidate: independent sigmoid,
                            NOT normalized against other participants - an
                            elimination signal used to shrink the focus
                            space, not a competing hypothesis. Multiple
                            people can simultaneously be "almost certainly
                            not the candidate".
  output_formatter.py  Builds the outbound EngineMessage: ranks by
                        probability_candidate, and produces
                        possible_candidate_ids - [] if nothing clears
                        INSUFFICIENT_EVIDENCE_THRESHOLD, a single id if one
                        participant clearly leads (clears CONFIDENT_THRESHOLD
                        with no one else within AMBIGUITY_MARGIN), or several
                        ids if multiple participants are in contention. The
                        system must never skip/misname the real candidate, so
                        ambiguity is reported explicitly rather than resolved
                        by an arbitrary tie-break. Evidence trail is attached
                        only to ids that made the cut.
  session_engine.py    The orchestrator ("Engine (continuous loop)" in the
                        diagram). Wires everything above together per
                        connection.

identifiers/            The actual pluggable, weighted identifiers.
                        Deliberately a mix of cheap rule-based signals
                        (always on, zero external dependency) and
                        LLM-backed semantic signals (litellm-based,
                        fail-open on any error/missing key) - see
                        core/llm_client.py.

  Rule-based:
  name_match.py          Display name vs candidate_name / interviewer_names
                          (fuzzy match). Weak by design - the reference
                          scenario exists specifically to punish anything
                          that treats name matching as authoritative.
  speaking_share.py       Share of total speaking time. Temporal/continuous.
  qa_pattern.py            Naive question-vs-answer transcript heuristic:
                            who asks interview questions vs who answers them.
  screenshare_heuristic.py  Very low-weight: sharing a screen is mildly
                             consistent with walking through a solution.
  silent_observer.py       Present a long time with zero speaking/webcam/
                            screenshare/transcript activity -> accrues
                            against_candidate evidence, decaying if they
                            later start participating. Targets "multiple
                            observers join silently".
  host_organizer.py        Display name vs calendar_invite.organizer.
                           Meeting organizers are essentially never the
                           candidate - independent of whether
                           candidate_name itself is right or stale, which
                           helps with "interviewer enters the wrong
                           candidate name".
  email_identity.py        Display name / ParticipantState.email vs
                            candidate_email. Strong, hard-to-fake signal
                            when an authenticated join email is available
                            (real adapters commonly provide this via SSO);
                            silent no-op on every scenario that doesn't set
                            one - forward-compatible plumbing, not dead code.

  LLM-backed (engine.core.llm_client, model configurable via LLM_MODEL,
  default fireworks_ai/accounts/fireworks/models/deepseek-v4-flash):
  llm_name_role.py          Semantic name/role classification - handles
                             nicknames, transliteration, suffixes like
                             "(Contractor)" that name_match's raw string
                             similarity can't. Runs alongside name_match,
                             not instead of it.
  llm_transcript_role.py    Batched, windowed transcript-behavior
                             classification (interviewee / interviewer /
                             observer / unclear) - the LLM-based upgrade
                             qa_pattern's own docstring calls out as the
                             natural next step, run alongside qa_pattern
                             rather than replacing it.
```

## LLM identifiers - fail-open contract

`core/llm_client.py`'s `structured_completion()` is the single call
site both LLM identifiers use. It returns either a validated pydantic
instance of the requested schema, or `None` - there is no third
outcome. A missing API key, a network timeout, a provider error, or a
response that fails schema validation all collapse to `None`, logged
once as a warning. Both LLM identifiers treat `None` exactly like "no
evidence this tick" - they never raise, and the engine's belief state
for a session with no LLM connectivity at all is identical to running
with just the rule-based identifiers. `tests/test_llm_identifiers.py`
exercises this contract directly (mocked completion, no network calls
in CI).

## One event, end to end

1. A `participant_join` SimEvent arrives. `state_store.apply_event()`
   creates a `ParticipantState` and returns `is_new=True`.
2. Because it's new, `SessionEngine._run_initial_identifiers()` runs -
   the diagram's "Initial One Time Run": every identifier with
   `run_mode in {ONE_TIME, BOTH}` gets its `on_join()` called once, with
   read-only state access.
3. The event is also published on `raw_bus` under its event type. Every
   identifier subscribed to that type (`CONTINUOUS`/`BOTH`) gets `on_event()`
   called.
4. Any `ctx.emit(evidence)` call from an identifier publishes on
   `evidence_bus`. The single subscriber there runs
   `evidence_normalizer.normalize()` then `belief_engine.apply()`, which
   updates the participant's two logit tracks and recomputes probabilities
   for *everyone* (softmax needs the whole pool).
5. `SessionEngine` calls `output_formatter.format_message()` and sends the
   resulting `EngineMessage` back down the WebSocket.

## Why two probabilities

Per the design notes on the diagram: we track probability of *being* the
candidate (the actual answer) and probability of *not being* the candidate
independently, because they answer different questions. Softmax-normalized
`probability_candidate` is a competition among current participants; an
independent sigmoid `probability_not_candidate` is a per-participant
elimination signal (e.g. "clearly an interviewer") that doesn't need to
trade off against anyone else's score, and multiple participants can
legitimately sit near 1.0 on it simultaneously. `belief_engine.is_eliminated()`
exposes a threshold on the latter for future use as a pruning hook (skip
expensive identifiers - e.g. CV/audio ML - for participants already
essentially ruled out), not yet wired into scheduling.

## What's explicitly out of scope for this layer

- Actually decoding `StreamFrame.data` (base64 audio/video/screenshare
  bytes). The state store tracks liveness/volume as a byte-count proxy
  only; any identifier that needs pixels or audio samples decodes them
  itself. Deepfake/voice-clone/behavioral-analysis identifiers are future
  pluggable additions, not part of the base engine layer.
- Multi-session/multi-process fan-out. `EventBus` is in-process only;
  swapping it for a real broker is the seam if the Engine needs to scale
  identifiers out as separate workers later.
- Auth and reconnect/resume on the WebSocket.
