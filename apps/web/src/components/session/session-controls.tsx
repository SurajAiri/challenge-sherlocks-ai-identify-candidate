"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Play, Square, Volume2, VolumeX } from "lucide-react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { useSessionStore } from "@/store/session-store";

const SPEED_OPTIONS = [1, 2, 4, 10, 20] as const;

export function SessionControls({
  scenarioId,
  onStart,
  onStop,
}: {
  scenarioId: string;
  onStart: () => void;
  onStop: () => void;
}) {
  const runStatus = useSessionStore((s) => s.runStatus);
  const runStartedAt = useSessionStore((s) => s.runStartedAt);
  const runError = useSessionStore((s) => s.runError);
  const audioPlaybackEnabled = useSessionStore((s) => s.audioPlaybackEnabled);
  const toggleAudioPlayback = useSessionStore((s) => s.toggleAudioPlayback);
  const runSpeedMultiplier = useSessionStore((s) => s.runSpeedMultiplier);
  const setRunSpeedMultiplier = useSessionStore((s) => s.setRunSpeedMultiplier);

  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (runStatus !== "streaming" || !runStartedAt) return;
    const id = setInterval(() => setElapsed((Date.now() - runStartedAt) / 1000), 250);
    return () => clearInterval(id);
  }, [runStatus, runStartedAt]);

  const running = runStatus === "connecting" || runStatus === "streaming";

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card px-4 py-3">
      <div className="flex items-center gap-3">
        {running ? (
          <Button variant="destructive" onClick={onStop}>
            <Square className="size-3.5" /> Stop
          </Button>
        ) : (
          <Button onClick={onStart} disabled={runStatus === "completed"}>
            <Play className="size-3.5" />
            {runStatus === "completed" ? "Run complete" : "Start experiment"}
          </Button>
        )}
        {runStatus === "streaming" && (
          <span className="font-mono text-sm text-muted-foreground">{formatElapsed(elapsed)}</span>
        )}
        {runError && <span className="text-xs text-destructive">{runError}</span>}
      </div>

      <div className="flex items-center gap-4">
        <label
          className="flex items-center gap-2 text-sm text-muted-foreground"
          title="Overrides the scenario's authored speed_multiplier for the next run. Takes effect at start only - can't be changed mid-stream, since it's the simulator's own clock being sped up, not client-side playback."
        >
          <span className="hidden sm:inline">Sim speed</span>
          <select
            className="rounded-md border border-border bg-background px-2 py-1 text-xs disabled:opacity-50"
            value={runSpeedMultiplier ?? ""}
            disabled={running}
            onChange={(e) =>
              setRunSpeedMultiplier(e.target.value === "" ? null : Number(e.target.value))
            }
          >
            <option value="">scenario default</option>
            {SPEED_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}x
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          {audioPlaybackEnabled ? <Volume2 className="size-4" /> : <VolumeX className="size-4" />}
          <span className="hidden sm:inline">Decode audio for playback</span>
          <Switch checked={audioPlaybackEnabled} onCheckedChange={toggleAudioPlayback} />
        </label>

        {runStatus === "completed" && (
          <Link href={`/session/${scenarioId}/result`} className={cn(buttonVariants({ variant: "secondary", size: "sm" }))}>
            View results <ArrowRight className="size-3.5" />
          </Link>
        )}
      </div>
    </div>
  );
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
