"use client";

import { LogOut, Mic, MicOff, MonitorUp, Video, VideoOff } from "lucide-react";

import { FrameCanvas } from "@/components/session/frame-canvas";
import { cn } from "@/lib/utils";
import type { ParticipantState } from "@/store/session-store";

/**
 * Deliberately does NOT render `role_hint` anywhere - the simulator
 * never sends it over the live `/run` wire (see `compiler.py`: only
 * whatever the author put in the authored event's `data` goes out,
 * and `role_hint` lives on `Participant`, not on any event payload).
 * Guessing who's the candidate is the whole point of the challenge;
 * this tile shows exactly what a real meeting adapter would give the
 * Engine, nothing more.
 */
export function ParticipantTile({ participant }: { participant: ParticipantState }) {
  const renamed = participant.nameHistory.length > 1;

  return (
    <div
      className={cn(
        "relative flex flex-col overflow-hidden rounded-lg border border-border bg-card transition-all",
        participant.speaking && "animate-speaking-pulse border-[var(--accent-signal)]/60",
        !participant.joined && "opacity-45 grayscale"
      )}
    >
      <div className="relative aspect-video w-full bg-black/40">
        {participant.webcamOn && participant.lastFrameDataUrl ? (
          <FrameCanvas frameDataUrl={participant.lastFrameDataUrl} className="size-full object-cover" />
        ) : (
          <div className="flex size-full items-center justify-center">
            <span className="flex size-12 items-center justify-center rounded-full bg-muted text-sm font-semibold text-muted-foreground">
              {initials(participant.displayName)}
            </span>
          </div>
        )}

        <div className="absolute top-2 left-2 flex items-center gap-1.5">
          {participant.screenshareOn && (
            <span className="flex items-center gap-1 rounded-md bg-black/60 px-1.5 py-0.5 text-[0.65rem] text-white">
              <MonitorUp className="size-3" /> Presenting
            </span>
          )}
          {!participant.joined && (
            <span className="flex items-center gap-1 rounded-md bg-black/60 px-1.5 py-0.5 text-[0.65rem] text-white">
              <LogOut className="size-3" /> Left
            </span>
          )}
        </div>

        <div className="absolute bottom-2 left-2 flex items-center gap-1">
          <IconBadge active={participant.micOn} onIcon={Mic} offIcon={MicOff} />
          <IconBadge active={participant.webcamOn} onIcon={Video} offIcon={VideoOff} />
        </div>
      </div>

      {/* Screenshare is its own video track (modality: "screenshare" -
          see applyStreamFrame in session-store.ts), decoded into
          lastScreenshareFrameDataUrl exactly like the webcam frame is.
          That data was already flowing correctly - it just had no
          consumer anywhere in the UI, so the only visible signal was
          the "Presenting" badge above. This renders the actual feed. */}
      {participant.screenshareOn && participant.lastScreenshareFrameDataUrl && (
        <div className="relative aspect-video w-full border-t border-border bg-black/60">
          <FrameCanvas
            frameDataUrl={participant.lastScreenshareFrameDataUrl}
            className="size-full object-contain"
          />
          <span className="absolute top-1.5 left-1.5 flex items-center gap-1 rounded-md bg-black/60 px-1.5 py-0.5 text-[0.65rem] text-white">
            <MonitorUp className="size-3" /> Screen
          </span>
        </div>
      )}

      <div className="flex items-center justify-between gap-2 px-2.5 py-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-foreground" title={participant.displayName}>
            {participant.displayName}
          </p>
          {renamed && (
            <p className="truncate text-[0.65rem] text-muted-foreground">
              also joined as: {participant.nameHistory.slice(0, -1).map((h) => h.name).join(", ")}
            </p>
          )}
        </div>
        <span className="shrink-0 font-mono text-[0.65rem] text-muted-foreground">
          {participant.participantId}
        </span>
      </div>
    </div>
  );
}

function IconBadge({
  active,
  onIcon: OnIcon,
  offIcon: OffIcon,
}: {
  active: boolean;
  onIcon: React.ComponentType<{ className?: string }>;
  offIcon: React.ComponentType<{ className?: string }>;
}) {
  const Icon = active ? OnIcon : OffIcon;
  return (
    <span
      className={cn(
        "flex size-5 items-center justify-center rounded-full",
        active ? "bg-[var(--accent-signal)]/25 text-[var(--accent-signal)]" : "bg-black/60 text-white/70"
      )}
    >
      <Icon className="size-3" />
    </span>
  );
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
