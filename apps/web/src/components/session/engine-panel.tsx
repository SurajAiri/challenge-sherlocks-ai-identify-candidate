"use client";

import { useState } from "react";
import { AlertTriangle, BrainCircuit, ChevronDown, History, RefreshCw, Telescope } from "lucide-react";

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
 * Renders the engine's live EngineMessage stream (see
 * engine/core/output_formatter.py): the current possible-candidate
 * set, the full ranked probability pool, the detection state, and the
 * evidence trail behind the leading hypothesis. Until the first
 * message arrives, everything falls back to an em dash.
 *
 * During the EXPLORING warmup phase (see detection_state.py) the panel
 * shows a dedicated banner instead of Candidate/Confidence/Reasoning
 * rows, because no candidate has been named yet by design.
 */
export function EnginePanel({
  onReconnect,
  groundTruthParticipantId,
}: {
  onReconnect?: () => void;
  /**
   * Read-only, for-testing display of the scenario's authored answer -
   * sourced the same way as the post-run results page
   * (scenario.groundTruthParticipantId from the local library entry,
   * itself populated by the simulator's author/scoring-only
   * `/evaluation` endpoint). Purely a rendering convenience passed down
   * from SessionClient: never touches session-store, never reaches the
   * Engine, and plays no part in the identification pipeline it's here
   * to help a human sanity-check against.
   */
  groundTruthParticipantId?: string | null;
}) {
  const status = useSessionStore((s) => s.engineStatus);
  const latest = useSessionStore((s) => s.engineLatest);
  const history = useSessionStore((s) => s.engineHistory);
  const participants = useSessionStore((s) => s.participants);
  const reconnectedMidRun = useSessionStore((s) => s.engineReconnectedMidRun);
  const [showHistory, setShowHistory] = useState(false);
  const [showRaw, setShowRaw] = useState(false);

  const displayName = (pid: string) => participants[pid]?.displayName ?? pid;
  const groundTruthName = groundTruthParticipantId
    ? (participants[groundTruthParticipantId]?.displayName ?? groundTruthParticipantId)
    : null;

  const isExploring = latest?.detectionState === "exploring";

  // The engine's verdict block is driven by possibleCandidateIds:
  // [] = still searching/exploring, 1 = confident pick, >1 = ambiguous.
  const possibleNames = latest?.possibleCandidateIds.map(displayName) ?? [];
  const candidateName = latest?.candidateParticipantId
    ? displayName(latest.candidateParticipantId)
    : null;
  const candidateLabel = candidateName
    ? candidateName
    : possibleNames.length > 1
      ? possibleNames.join(" / ")
      : null;

  const canReconnect =
    onReconnect &&
    (status === "disconnected" || status === "error" || status === "idle");

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-sm font-medium">
          <BrainCircuit className="size-4 text-[var(--accent-signal)]" />
          Engine
        </span>
        <div className="flex items-center gap-2">
          <StatusDot tone={STATUS_TONE[status]} label={STATUS_LABEL[status]} />
          {canReconnect && (
            <button
              type="button"
              onClick={onReconnect}
              title="Reconnect to engine"
              className="flex items-center gap-1 rounded-md border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <RefreshCw className="size-3" />
              Reconnect
            </button>
          )}
        </div>
      </div>

      {/* Mid-run reconnect warning — the engine has no session resume
          yet (see apps/engine/src/engine/api/ws.py docstring): a
          reconnect after the socket had already opened once means the
          server spun up a brand-new, empty SessionEngine. Everything
          in history/probabilities from here on is that fresh instance
          re-deriving from scratch, not a recovery of what came before.
          Persistent for the rest of the run (not dismissible) since it
          stays true for every prediction that follows. */}
      {reconnectedMidRun && (
        <div className="flex items-start gap-2.5 rounded-lg border border-destructive/25 bg-destructive/8 px-3 py-2.5">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" />
          <div className="flex flex-col gap-0.5">
            <p className="text-xs font-medium text-destructive">Engine reconnected mid-run</p>
            <p className="text-[0.7rem] leading-snug text-destructive/80">
              The connection dropped and reconnected during this run. The engine has no
              session resume yet, so this is a fresh instance re-deriving from scratch —
              predictions before this point are from a discarded session.
            </p>
          </div>
        </div>
      )}

      {/* Ground truth (for testing) — read-only, never fed to the engine.
          Shown here (not just on the post-run results page) so you can
          watch the live prediction against the authored answer in real
          time instead of waiting for the run to finish. */}
      {groundTruthParticipantId !== undefined && (
        <div className="flex items-center justify-between text-xs">
          <span className="text-muted-foreground">Ground truth (test only)</span>
          <span className="font-mono text-muted-foreground">
            {groundTruthName ?? "not recorded"}
          </span>
        </div>
      )}

      {/* EXPLORING warmup banner */}
      {isExploring && latest ? (
        <div className="flex flex-col gap-2">
          <div className="flex items-start gap-2.5 rounded-lg border border-blue-500/20 bg-blue-500/8 px-3 py-2.5">
            <Telescope className="mt-0.5 size-4 shrink-0 animate-pulse text-blue-400" />
            <div className="flex flex-col gap-0.5">
              <p className="text-xs font-medium text-blue-300">Exploring…</p>
              <p className="text-[0.7rem] leading-snug text-blue-300/70">
                Gathering initial signal. The engine won&apos;t name a candidate until
                enough time and evidence have accumulated.
              </p>
            </div>
          </div>
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">State</span>
            <DetectionStateBadge state="exploring" />
          </div>
        </div>
      ) : (
        /* Normal candidate / confidence / reasoning rows */
        <dl className="flex flex-col gap-2.5 text-sm">
          <Row label="Candidate" value={candidateLabel ?? "—"} mono={!candidateLabel} />
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
          {latest && (
            <Row
              label="State"
              value={<DetectionStateBadge state={latest.detectionState} />}
            />
          )}
          <div>
            <dt className="text-xs text-muted-foreground">Reasoning</dt>
            <dd className="mt-0.5 text-sm text-foreground/90">
              {latest?.reasoning ?? <span className="text-muted-foreground">—</span>}
            </dd>
          </div>
        </dl>
      )}

      {/* Probability bar chart — visible in ALL states, including warmup,
          so the user can watch signal building even before a candidate is named */}
      {latest && latest.probabilityBeingCandidate.length > 0 && (
        <div className="border-t border-border pt-2">
          <p className="mb-1.5 text-xs text-muted-foreground">
            {isExploring ? "Signal building (no candidate named yet)" : "Candidate probabilities"}
          </p>
          <ul className="flex flex-col gap-1.5">
            {latest.probabilityBeingCandidate.map(([pid, p]) => (
              <li key={pid} className="flex items-center gap-2 text-xs">
                <span
                  className={cn(
                    "min-w-0 flex-1 truncate",
                    !isExploring && latest.possibleCandidateIds.includes(pid)
                      ? "font-medium text-foreground"
                      : "text-muted-foreground"
                  )}
                >
                  {displayName(pid)}
                  {groundTruthParticipantId === pid && (
                    <span
                      title="Ground truth (test only)"
                      className="ml-1 text-[var(--accent-signal)]"
                    >
                      ✓
                    </span>
                  )}
                </span>
                <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
                  <span
                    className={cn(
                      "block h-full rounded-full transition-all duration-500",
                      isExploring
                        ? "bg-blue-400/50"
                        : p >= 0.7
                          ? "bg-emerald-400"
                          : p >= 0.4
                            ? "bg-amber-400"
                            : "bg-muted-foreground/50"
                    )}
                    style={{ width: `${Math.round(p * 100)}%` }}
                  />
                </span>
                <span className="w-9 text-right font-mono text-muted-foreground">
                  {Math.round(p * 100)}%
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Prediction history */}
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
            <ChevronDown
              className={cn("size-3.5 transition-transform", showHistory && "rotate-180")}
            />
          </button>
          {showHistory && (
            <ul className="scrollbar-thin mt-2 flex max-h-40 flex-col gap-1.5 overflow-y-auto font-mono text-[0.7rem] text-muted-foreground">
              {[...history].reverse().map((p) => (
                <li key={p.id} className="flex items-center gap-2">
                  <span className="w-10 shrink-0">{p.t.toFixed(1)}s</span>
                  <span
                    className={cn(
                      "min-w-0 flex-1 truncate",
                      p.detectionState === "exploring" && "italic text-blue-400/70"
                    )}
                  >
                    {p.detectionState === "exploring"
                      ? "exploring…"
                      : p.candidateParticipantId
                        ? (participants[p.candidateParticipantId]?.displayName ??
                          p.candidateParticipantId)
                        : "—"}
                  </span>
                  <span>
                    {p.detectionState === "exploring"
                      ? ""
                      : p.confidence != null
                        ? `${Math.round(p.confidence * 100)}%`
                        : "—"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Raw message toggle */}
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

function Row({
  label,
  value,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn("text-sm font-medium text-foreground", mono && "font-mono text-muted-foreground")}>
        {value}
      </dd>
    </div>
  );
}

const DETECTION_STATE_STYLES: Record<string, { label: string; className: string }> = {
  exploring: {
    label: "exploring",
    className: "bg-blue-500/15 text-blue-300",
  },
  searching: {
    label: "searching",
    className: "bg-muted text-muted-foreground",
  },
  likely_candidate: {
    label: "likely candidate",
    className: "bg-amber-500/15 text-amber-300",
  },
  stable_candidate: {
    label: "stable candidate",
    className: "bg-emerald-500/15 text-emerald-300",
  },
  lost_candidate: {
    label: "lost candidate",
    className: "bg-destructive/15 text-destructive",
  },
};

function DetectionStateBadge({ state }: { state: string }) {
  const style = DETECTION_STATE_STYLES[state] ?? {
    label: state,
    className: "bg-muted text-muted-foreground",
  };
  return (
    <span className={cn("rounded-full px-2 py-0.5 font-mono text-[0.65rem]", style.className)}>
      {style.label}
    </span>
  );
}

function ConfidenceMeter({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const tone =
    pct >= 70 ? "bg-emerald-400" : pct >= 40 ? "bg-amber-400" : "bg-destructive";
  return (
    <span className="flex items-center gap-2">
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
        <span
          className={cn("block h-full rounded-full", tone)}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="font-mono text-xs">{Math.round(pct)}%</span>
    </span>
  );
}

