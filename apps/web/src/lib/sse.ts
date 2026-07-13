/**
 * Minimal Server-Sent-Events parser for a `fetch()` Response stream.
 *
 * The simulator's `/run` endpoint is POST (scenario_dir lives in the
 * body), so the native `EventSource` API is off the table - it only
 * ever issues GETs. This reads the raw `ReadableStream` instead and
 * splits on blank lines, same framing `describe_event`/`_sse` on the
 * Python side produce: `event: <kind>\ndata: <json>\n\n`.
 */

export interface RawSSEFrame {
  event: string;
  data: string;
}

export async function* iterateSSE(
  response: Response,
  signal?: AbortSignal
): AsyncGenerator<RawSSEFrame> {
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const abortHandler = () => reader.cancel().catch(() => {});
  signal?.addEventListener("abort", abortHandler);

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const frame = parseFrame(raw);
        if (frame) yield frame;
      }
    }
    // Flush any trailing frame without a final blank line.
    if (buffer.trim()) {
      const frame = parseFrame(buffer);
      if (frame) yield frame;
    }
  } finally {
    signal?.removeEventListener("abort", abortHandler);
  }
}

function parseFrame(raw: string): RawSSEFrame | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  return { event, data: dataLines.join("\n") };
}
