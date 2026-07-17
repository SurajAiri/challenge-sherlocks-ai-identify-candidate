# Simulator

The **Scenario Simulator** (`apps/simulator`) replays pre-authored YAML scenario files as a stream of raw meeting events over WebSocket. It lets you test the Belief Engine against controlled, repeatable situations — participants joining under pseudonyms, silent observers, multi-interviewer calls, late name reveals — without needing a live video platform.

The simulator has **no inference logic**. It only emits events; the Engine does the thinking.

---

## How it works

1. A **scenario YAML file** describes the cast of participants, the calendar context (candidate name, interviewers, email), and a timeline of events.
2. The **Scenario Compiler** validates and compiles the YAML into a sequence of typed `SimEvent` objects.
3. The simulator streams those events over WebSocket at configurable speed (1×, 2×, …) to the Dashboard, which relays them to the Engine.

---

## Documentation

| Document | Audience | Contents |
|---|---|---|
| [Scenario Authoring](../apps/simulator/docs/SCENARIO_AUTHORING.md) | Humans | Full reference for writing and editing `.yaml` scenario files — event types, participant fields, timing, difficulty ratings |
| [SKILL.md](../apps/simulator/docs/SKILL.md) | AI agents | Structured instructions for an AI to generate valid scenario YAML files from a natural-language description |

---

## Scenario files

Scenarios live in `apps/simulator/scenarios/`. Each file is a self-contained test case with:

- **Context block** — candidate name, email, calendar title, list of interviewers
- **Participants block** — who is in the call, their display names, roles
- **Events timeline** — ordered list of `participant_joined`, `transcript`, `screen_share_started`, `participant_update`, … events
- **Stress notes** — what edge cases this scenario was designed to exercise (shown on the results page)

See [Scenario Authoring](../apps/simulator/docs/SCENARIO_AUTHORING.md) for the full field reference.