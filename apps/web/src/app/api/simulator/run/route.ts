import { NextRequest } from "next/server";
import { SIMULATOR_BASE_URL } from "@/lib/simulator-server";

// SSE streams are inherently dynamic/long-lived - opt this route out of
// any static/edge caching behavior.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/**
 * Passes the scenario_dir straight through to the simulator's `/run`
 * and streams its SSE response body back verbatim. The Next.js server
 * is a same-origin hop for the browser (no CORS to configure on the
 * FastAPI side) and a plain Node `fetch` to the simulator (no CORS
 * restrictions server-to-server either).
 */
export async function POST(req: NextRequest) {
  const body = await req.text();

  let upstream: Response;
  try {
    upstream = await fetch(`${SIMULATOR_BASE_URL}/run`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
    });
  } catch (err) {
    return Response.json(
      {
        error: "simulator_unreachable",
        detail: `Could not reach the simulator at ${SIMULATOR_BASE_URL}. Is it running?`,
        cause: err instanceof Error ? err.message : String(err),
      },
      { status: 502 }
    );
  }

  if (!upstream.ok || !upstream.body) {
    const text = await upstream.text().catch(() => "");
    return new Response(text || "simulator returned an error", {
      status: upstream.status || 502,
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
