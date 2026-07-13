"use client";

import { useEffect, useRef } from "react";
import { MessageSquare, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useSessionStore } from "@/store/session-store";

export function TranscriptPanel() {
  const transcript = useSessionStore((s) => s.transcript);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [transcript.length]);

  return (
    <div className="flex flex-1 flex-col overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <MessageSquare className="size-4 text-muted-foreground" />
        <span className="text-sm font-medium">Live transcript</span>
      </div>
      <div ref={scrollRef} className="scrollbar-thin flex-1 overflow-y-auto px-3 py-2">
        {transcript.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            Transcript segments will appear here as participants speak.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {transcript.map((segment) => (
              <li key={segment.id} className="flex items-start gap-2">
                <AudioButton audioBlobUrl={segment.audioBlobUrl} pending={segment.audioPending} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2">
                    <span className="text-sm font-medium text-foreground">{segment.displayName}</span>
                    <span className="font-mono text-[0.65rem] text-muted-foreground">
                      {formatTime(segment.t)}
                    </span>
                  </div>
                  <p className="text-sm text-muted-foreground">{segment.text}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function AudioButton({ audioBlobUrl, pending }: { audioBlobUrl: string | null; pending: boolean }) {
  const audioRef = useRef<HTMLAudioElement | null>(null);

  if (!audioBlobUrl) {
    return (
      <Button
        variant="ghost"
        size="icon-xs"
        disabled
        className="mt-0.5 shrink-0"
        title={pending ? "Buffering audio…" : "No audio track for this segment"}
      >
        <Play className="size-3" />
      </Button>
    );
  }

  return (
    <>
      <Button
        variant="ghost"
        size="icon-xs"
        className="mt-0.5 shrink-0 text-[var(--accent-signal)]"
        onClick={() => audioRef.current?.play()}
        title="Play"
      >
        <Play className="size-3" />
      </Button>
      <audio ref={audioRef} src={audioBlobUrl} preload="none" className="hidden" />
    </>
  );
}

function formatTime(t: number): string {
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
