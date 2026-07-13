"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, ScrollText } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { useSessionStore } from "@/store/session-store";

const KIND_VARIANT = {
  context: "outline",
  event: "accent",
  stream: "default",
  error: "destructive",
} as const;

export function RawEventLog() {
  const rawLog = useSessionStore((s) => s.rawLog);
  const [open, setOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [rawLog.length, open]);

  return (
    <div className="flex flex-col rounded-xl border border-border bg-card">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-sm font-medium"
      >
        <span className="flex items-center gap-2">
          <ScrollText className="size-4 text-muted-foreground" />
          Raw event log
          <span className="font-mono text-xs text-muted-foreground">({rawLog.length})</span>
        </span>
        <ChevronDown className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div ref={scrollRef} className="scrollbar-thin max-h-64 overflow-y-auto border-t border-border px-3 py-2">
          {rawLog.length === 0 ? (
            <p className="py-4 text-center text-xs text-muted-foreground">No events yet.</p>
          ) : (
            <ul className="flex flex-col gap-1 font-mono text-[0.7rem] text-muted-foreground">
              {rawLog.map((entry) => (
                <li key={entry.id} className="flex items-start gap-2">
                  <span className="w-12 shrink-0 text-right">{entry.t.toFixed(1)}s</span>
                  <Badge variant={KIND_VARIANT[entry.kind]} className="shrink-0 font-mono">
                    {entry.kind}
                  </Badge>
                  <span className="break-all">{entry.summary}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
