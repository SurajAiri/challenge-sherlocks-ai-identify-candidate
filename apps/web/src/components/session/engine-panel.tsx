"use client";

import { useState } from "react";
import { BrainCircuit, ChevronDown, History } from "lucide-react";

import { StatusDot, type StatusTone } from "@/components/status-dot";
import { cn } from "@/lib/utils";
import type { EngineStatus } from "@/lib/engine-client";
import { useSessionStore } from "@/store/session-store";

const STATUS_TONE: Record<EngineStatus, StatusTone> = {
  idle: "neutral",
  connecting: "warn",
  connected: "live",
  disconnected: "bad",
  error: "bad",
};

const STATUS_LABEL: Record<EngineStatus, string> = {
  idle: "Not connected",
  connecting: "Connecting…",
  connected: "Connected",
  disconnected: "Disconnected — retrying",
  error: "Connection error — retrying",
};

/**
 * This is intentionally a slot, not a feature: every value below falls
 * back to an em dash until a real message arrives over the Engine
 * WebSocket. Wiring the real Engine up later means only ever changing
 * what's on the other end of that socket - this component, the store
 * shape, and the message parsing in `session-store.ts` shouldn't need
 * to change.
 */
export function EnginePanel() {
  const status = useSessionStore((s) => s.engineStatus);
  const latest = useSessionStore((s) => s.engineLatest);
  const history = useSessionStore((s) => s.engineHistory);
  const participants = useSessionStore((s) => s.participants);
  const [showHistory, setShowHistory] = useState(false);
  const [showRaw, setShowRaw] = useState(false);

  const candidateName = latest?.candidateParticipantId
    ? participants[latest.candidateParticipantId]?.displayName ?? latest.candidateParticipantId
    : null;

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-sm font-medium">
          <BrainCircuit className="size-4 text-[var(--accent-signal)]" />
          Engine
        </span>
        <StatusDot tone={STATUS_TONE[status]} label={STATUS_LABEL[status]} />
      </div>

      <dl className="flex flex-col gap-2.5 text-sm">
        <Row label="Candidate" value={candidateName ?? "—"} mono={!candidateName} />
        <Row
          label="Confidence"
          value={
            latest?.confidence != null ? (
              <ConfidenceMeter value={latest.confidence} />
            ) : (
              "—"
            )
          }
        />
        <div>
          <dt className="text-xs text-muted-foreground">Reasoning</dt>
          <dd className="mt-0.5 text-sm text-foreground/90">
            {latest?.reasoning ?? <span className="text-muted-foreground">—</span>}
          </dd>
        </div>
      </dl>

      {history.length > 0 && (
        <div className="border-t border-border pt-2">
          <button
            type="button"
            onClick={() => setShowHistory((v) => !v)}
            className="flex w-full items-center justify-between text-xs text-muted-foreground"
          >
            <span className="flex items-center gap-1.5">
              <History className="size-3.5" /> Prediction history ({history.length})
            </span>
            <ChevronDown className={cn("size-3.5 transition-transform", showHistory && "rotate-180")} />
          </button>
          {showHistory && (
            <ul className="scrollbar-thin mt-2 flex max-h-40 flex-col gap-1.5 overflow-y-auto font-mono text-[0.7rem] text-muted-foreground">
              {[...history].reverse().map((p) => (
                <li key={p.id} className="flex items-center gap-2">
                  <span className="w-10 shrink-0">{p.t.toFixed(1)}s</span>
                  <span className="min-w-0 flex-1 truncate">
                    {p.candidateParticipantId ? participants[p.candidateParticipantId]?.displayName ?? p.candidateParticipantId : "—"}
                  </span>
                  <span>{p.confidence != null ? `${Math.round(p.confidence * 100)}%` : "—"}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {latest && (
        <div className="border-t border-border pt-2">
          <button
            type="button"
            onClick={() => setShowRaw((v) => !v)}
            className="text-xs text-muted-foreground underline decoration-dotted underline-offset-2"
          >
            {showRaw ? "Hide raw message" : "Show raw message"}
          </button>
          {showRaw && (
            <pre className="scrollbar-thin mt-2 max-h-32 overflow-auto rounded-md bg-muted p-2 text-[0.65rem] text-muted-foreground">
              {JSON.stringify(latest.raw, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn("text-sm font-medium text-foreground", mono && "font-mono text-muted-foreground")}>
        {value}
      </dd>
    </div>
  );
}

function ConfidenceMeter({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const tone = pct >= 70 ? "bg-emerald-400" : pct >= 40 ? "bg-amber-400" : "bg-destructive";
  return (
    <span className="flex items-center gap-2">
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
        <span className={cn("block h-full rounded-full", tone)} style={{ width: `${pct}%` }} />
      </span>
      <span className="font-mono text-xs">{Math.round(pct)}%</span>
    </span>
  );
}
