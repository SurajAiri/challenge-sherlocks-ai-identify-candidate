import { NextRequest } from "next/server";
import { SIMULATOR_BASE_URL } from "@/lib/simulator-server";

export const dynamic = "force-dynamic";

/**
 * Proxies the simulator's /evaluation endpoint. This is the one
 * endpoint allowed to return `ground_truth_participant_id` - it's used
 * here only to populate the scenario library card and the post-run
 * results/scoring page, never forwarded into the live Engine path
 * (the `/api/simulator/run` proxy is the only thing that touches that
 * path, and it never calls this route).
 */
export async function POST(req: NextRequest) {
  const body = await req.text();

  let upstream: Response;
  try {
    upstream = await fetch(`${SIMULATOR_BASE_URL}/evaluation`, {
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

  const text = await upstream.text();
  return new Response(text, {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}
