/**
 * WebSocket client for the Engine.
 *
 * The Engine doesn't exist yet (per the architecture diagram it's the
 * next piece to build), so this is deliberately defensive: it never
 * throws on a failed/refused connection, retries with backoff, and
 * treats every inbound message as untyped JSON-or-string. Wiring the
 * real Engine up later should mean pointing `NEXT_PUBLIC_ENGINE_WS_URL`
 * at it - nothing here should need to change.
 */

export type EngineStatus = "idle" | "connecting" | "connected" | "disconnected" | "error";

type MessageListener = (raw: unknown) => void;
type StatusListener = (status: EngineStatus) => void;

const RECONNECT_DELAY_MS = 3000;

export class EngineSocket {
  private ws: WebSocket | null = null;
  private status: EngineStatus = "idle";
  private manualClose = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private messageListeners = new Set<MessageListener>();
  private statusListeners = new Set<StatusListener>();

  constructor(private url: string) {}

  connect() {
    this.manualClose = false;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    this.setStatus("connecting");
    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.setStatus("error");
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => this.setStatus("connected");
    this.ws.onmessage = (ev) => {
      let parsed: unknown = ev.data;
      if (typeof ev.data === "string") {
        try {
          parsed = JSON.parse(ev.data);
        } catch {
          // leave as raw string - a "raw message" fallback view can still show it
        }
      }
      this.messageListeners.forEach((l) => l(parsed));
    };
    this.ws.onerror = () => this.setStatus("error");
    this.ws.onclose = () => {
      this.setStatus("disconnected");
      if (!this.manualClose) this.scheduleReconnect();
    };
  }

  send(payload: unknown): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(typeof payload === "string" ? payload : JSON.stringify(payload));
      return true;
    }
    return false;
  }

  onMessage(listener: MessageListener) {
    this.messageListeners.add(listener);
    return () => this.messageListeners.delete(listener);
  }

  onStatus(listener: StatusListener) {
    this.statusListeners.add(listener);
    listener(this.status);
    return () => this.statusListeners.delete(listener);
  }

  close() {
    this.manualClose = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => this.connect(), RECONNECT_DELAY_MS);
  }

  private setStatus(status: EngineStatus) {
    this.status = status;
    this.statusListeners.forEach((l) => l(status));
  }
}

export function getEngineWsUrl(): string {
  return process.env.NEXT_PUBLIC_ENGINE_WS_URL ?? "ws://localhost:8090/ws";
}
