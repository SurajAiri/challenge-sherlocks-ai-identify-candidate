/**
 * Browser-side driver for the simulator's `/run` SSE stream.
 *
 * Talks to our own `/api/simulator/run` route (a thin same-origin
 * proxy - see `app/api/simulator/run/route.ts`) rather than the
 * Python service directly, so we don't have to reason about CORS on
 * the FastAPI side at all.
 */
import { iterateSSE } from "@/lib/sse";
import {
  sessionContextSchema,
  simEventSchema,
  streamFrameSchema,
  type SimFrame,
} from "@/lib/types";

export interface SimulatorRunHandlers {
  onFrame: (frame: SimFrame) => void;
  onOpen?: () => void;
  onDone?: () => void;
  onError?: (message: string) => void;
}

export function startSimulatorRun(
  scenarioDir: string,
  handlers: SimulatorRunHandlers,
  // Overrides the scenario's authored controls.speed_multiplier for this
  // run only (see apps/simulator/src/simulator/api.py ScenarioRequest).
  // undefined/null = use whatever index.yml authored, same as before
  // this param existed. This only affects the simulator's own pacing of
  // the SSE stream at request time - it is NOT a live/mid-stream knob
  // and has nothing to do with replaying an already-completed run.
  speedMultiplier?: number | null
): AbortController {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await fetch("/api/simulator/run", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          scenario_dir: scenarioDir,
          ...(speedMultiplier ? { speed_multiplier: speedMultiplier } : {}),
        }),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        const text = await res.text().catch(() => "");
        handlers.onError?.(text || `simulator returned ${res.status}`);
        return;
      }

      handlers.onOpen?.();

      for await (const raw of iterateSSE(res, controller.signal)) {
        const frame = toSimFrame(raw.event, raw.data);
        if (frame) handlers.onFrame(frame);
      }

      handlers.onDone?.();
    } catch (err) {
      if (controller.signal.aborted) return;
      handlers.onError?.(err instanceof Error ? err.message : String(err));
    }
  })();

  return controller;
}

function toSimFrame(event: string, data: string): SimFrame | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    return { kind: "error", payload: `unparseable frame: ${data.slice(0, 200)}` };
  }

  switch (event) {
    case "context": {
      const result = sessionContextSchema.safeParse(parsed);
      return result.success
        ? { kind: "context", payload: result.data }
        : { kind: "error", payload: result.error.message };
    }
    case "event": {
      const result = simEventSchema.safeParse(parsed);
      return result.success
        ? { kind: "event", payload: result.data }
        : { kind: "error", payload: result.error.message };
    }
    case "stream": {
      const result = streamFrameSchema.safeParse(parsed);
      return result.success
        ? { kind: "stream", payload: result.data }
        : { kind: "error", payload: result.error.message };
    }
    case "error":
      return { kind: "error", payload: parsed };
    default:
      return { kind: "error", payload: `unknown SSE event kind: ${event}` };
  }
}
