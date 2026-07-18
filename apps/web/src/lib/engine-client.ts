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

/**
 * Fires once, the moment a *second* (or later) successful open happens
 * on the same EngineSocket instance. The engine's own ws.py docstring
 * is explicit that there's "no session multiplexing, no
 * reconnection/resume support yet" - every accepted connection spins
 * up a brand-new, empty SessionEngine (see session_engine.py:
 * ParticipantStateRepository/FeatureCache/BeliefEngine all constructed
 * fresh in __init__). So a mid-run drop + auto-reconnect doesn't
 * "recover" anything: it silently starts identification over from
 * zero on the server while the client just shows "Connected" again,
 * with no indication that everything accumulated so far was lost.
 * This callback exists so the UI can say so instead of hiding it.
 */
type ReconnectListener = () => void;

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
  // Frames sent before the socket reaches OPEN (e.g. handleStart fires
  // connect() and the simulator's first frames can arrive before the WS
  // handshake finishes) used to be dropped silently by send() below,
  // which is exactly what produced "playback started but the engine
  // never saw the early events" - the drop was invisible because
  // send() only returned a boolean nobody checked. Buffer them here
  // instead and flush in arrival order the instant onopen fires, so no
  // frame sent while "connecting" is ever lost to the race.
  private pendingQueue: unknown[] = [];
  private hasOpenedOnce = false;
  private reconnectListeners = new Set<ReconnectListener>();

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

    this.ws.onopen = () => {
      this.setStatus("connected");
      if (this.hasOpenedOnce) {
        // Second+ open on this instance = a fresh, stateless
        // SessionEngine on the server. Tell the UI so it can warn
        // instead of implying a seamless resume.
        this.reconnectListeners.forEach((l) => l());
      }
      this.hasOpenedOnce = true;
      this.flushQueue();
    };
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
      // manualClose means this close was requested by us (run completed,
      // run stopped, or a fresh connect() superseding this socket) - not
      // an unexpected drop. Reporting it as "disconnected — retrying" in
      // that case was misleading: the label implies something went
      // wrong and a reconnect is coming, when actually nothing is
      // wrong and nothing will retry (scheduleReconnect is skipped
      // below either way). Only report the alarming state when it's
      // actually true.
      this.setStatus(this.manualClose ? "idle" : "disconnected");
      if (!this.manualClose) this.scheduleReconnect();
    };
  }

  /**
   * Always "succeeds" from the caller's perspective while the socket is
   * still connecting/reconnecting: the frame is queued and flushed as
   * soon as the connection opens, in the same order it was sent. Only
   * returns false once we know for certain nothing is coming (manually
   * closed, i.e. after Stop/Done) - that's the one case where silently
   * queuing forever would be wrong.
   */
  send(payload: unknown): boolean {
    if (this.manualClose) return false;
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(typeof payload === "string" ? payload : JSON.stringify(payload));
      return true;
    }
    this.pendingQueue.push(payload);
    return true;
  }

  private flushQueue() {
    if (!this.pendingQueue.length) return;
    const queued = this.pendingQueue;
    this.pendingQueue = [];
    for (const payload of queued) {
      this.ws?.send(typeof payload === "string" ? payload : JSON.stringify(payload));
    }
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

  onReconnectDetected(listener: ReconnectListener) {
    this.reconnectListeners.add(listener);
    return () => this.reconnectListeners.delete(listener);
  }

  close() {
    this.manualClose = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
    // Drop anything still queued - a Stop/Done close means this run is
    // over, so flushing these into a *future* connect() would replay
    // stale frames from the previous run into the next one.
    this.pendingQueue = [];
    this.hasOpenedOnce = false;
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
