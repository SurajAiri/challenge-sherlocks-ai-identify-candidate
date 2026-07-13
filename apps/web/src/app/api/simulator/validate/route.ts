import { NextRequest } from "next/server";
import { SIMULATOR_BASE_URL } from "@/lib/simulator-server";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const body = await req.text();

  let upstream: Response;
  try {
    upstream = await fetch(`${SIMULATOR_BASE_URL}/validate`, {
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
