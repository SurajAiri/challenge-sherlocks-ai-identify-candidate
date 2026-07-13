"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { Logo } from "@/components/logo";
import { StatusDot, type StatusTone } from "@/components/status-dot";
import type { EngineStatus } from "@/lib/engine-client";
import type { RunStatus } from "@/store/session-store";

const RUN_TONE: Record<RunStatus, StatusTone> = {
  idle: "neutral",
  connecting: "warn",
  streaming: "live",
  completed: "good",
  error: "bad",
};

const RUN_LABEL: Record<RunStatus, string> = {
  idle: "Not started",
  connecting: "Connecting to simulator…",
  streaming: "Streaming",
  completed: "Run complete",
  error: "Error",
};

const ENGINE_TONE: Record<EngineStatus, StatusTone> = {
  idle: "neutral",
  connecting: "warn",
  connected: "live",
  disconnected: "bad",
  error: "bad",
};

export function Topbar({
  scenarioName,
  runStatus,
  engineStatus,
}: {
  scenarioName: string;
  runStatus: RunStatus;
  engineStatus: EngineStatus;
}) {
  return (
    <header className="flex items-center justify-between gap-4 border-b border-border px-6 py-3">
      <div className="flex min-w-0 items-center gap-3">
        <Link
          href="/"
          className="flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-4" />
        </Link>
        <Logo />
        <span className="truncate text-sm font-medium text-foreground">{scenarioName}</span>
      </div>
      <div className="flex shrink-0 items-center gap-4">
        <StatusDot tone={RUN_TONE[runStatus]} label={`Simulator: ${RUN_LABEL[runStatus]}`} />
        <StatusDot tone={ENGINE_TONE[engineStatus]} label={`Engine: ${engineStatus}`} />
      </div>
    </header>
  );
}
