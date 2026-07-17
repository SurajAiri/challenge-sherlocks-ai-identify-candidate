"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Play, RotateCcw, Square, Volume2, VolumeX } from "lucide-react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { useSessionStore } from "@/store/session-store";

const SPEED_OPTIONS = [1, 2, 4, 10, 20] as const;

// Above this, the live playback path (session-store.ts liveAudioChunkQueue)
// falls further behind every utterance instead of catching up, since
// each queued clip still takes its own real, un-sped-up duration to
// play while the sim clock producing the next one is scaled by
// speed_multiplier. See LiveAudioPlayer's doc comment.
const MAX_SPEED_FOR_LIVE_AUDIO = 8;

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
  const livePlaybackEnabled = useSessionStore((s) => s.livePlaybackEnabled);
  const setLivePlaybackEnabled = useSessionStore((s) => s.setLivePlaybackEnabled);
  const runSpeedMultiplier = useSessionStore((s) => s.runSpeedMultiplier);
  const setRunSpeedMultiplier = useSessionStore((s) => s.setRunSpeedMultiplier);

  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (runStatus !== "streaming" || !runStartedAt) return;
    const id = setInterval(() => setElapsed((Date.now() - runStartedAt) / 1000), 250);
    return () => clearInterval(id);
  }, [runStatus, runStartedAt]);

  const running = runStatus === "connecting" || runStatus === "streaming";

  // null ("scenario default") is deliberately treated as "unknown, so
  // assume too fast" rather than "assume 1x" - index.yml's own
  // speed_multiplier is never surfaced to this client (only an explicit
  // override is), and this demo's own scenario defaults to 20x, so
  // guessing 1x here would be actively wrong more often than not.
  const liveAudioAllowed = runSpeedMultiplier !== null && runSpeedMultiplier <= MAX_SPEED_FOR_LIVE_AUDIO;

  useEffect(() => {
    if (!liveAudioAllowed && livePlaybackEnabled) setLivePlaybackEnabled(false);
  }, [liveAudioAllowed, livePlaybackEnabled, setLivePlaybackEnabled]);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card px-4 py-3">
      <div className="flex items-center gap-3">
        {running ? (
          <Button variant="destructive" onClick={onStop}>
            <Square className="size-3.5" /> Stop
          </Button>
        ) : runStatus === "completed" ? (
          <Button onClick={onStart}>
            <RotateCcw className="size-3.5" />
            Try again
          </Button>
        ) : (
          <Button onClick={onStart}>
            <Play className="size-3.5" />
            Start experiment
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
          title="Sim speed is locked at 1x — the engine cannot run at higher speeds due to identifier run limits."
        >
          <span className="hidden sm:inline">Sim speed</span>
          <select
            className="rounded-md border border-border bg-background px-2 py-1 text-xs disabled:opacity-50"
            value={runSpeedMultiplier ?? 1}
            disabled
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

        <label
          className={cn(
            "flex items-center gap-2 text-sm text-muted-foreground",
            !liveAudioAllowed && "opacity-50"
          )}
          title={
            liveAudioAllowed
              ? "Auto-play each participant's audio as their utterance finishes decoding."
              : "Disabled above 8x (or when using scenario default speed, which may exceed it): playback can't keep up with how fast utterances are produced, so it would just fall further and further behind."
          }
        >
          {livePlaybackEnabled ? <Volume2 className="size-4" /> : <VolumeX className="size-4" />}
          <span className="hidden sm:inline">Play audio</span>
          <Switch
            checked={livePlaybackEnabled}
            disabled={!liveAudioAllowed}
            onCheckedChange={setLivePlaybackEnabled}
          />
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
