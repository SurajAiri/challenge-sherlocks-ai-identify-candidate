import { cn } from "@/lib/utils";

export type StatusTone = "neutral" | "good" | "warn" | "bad" | "live";

const toneClass: Record<StatusTone, string> = {
  neutral: "bg-muted-foreground/50",
  good: "bg-emerald-400",
  warn: "bg-amber-400",
  bad: "bg-destructive",
  live: "bg-[var(--accent-signal)] animate-live-dot",
};

export function StatusDot({
  tone,
  label,
  className,
}: {
  tone: StatusTone;
  label?: string;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs text-muted-foreground", className)}>
      <span className={cn("size-1.5 rounded-full", toneClass[tone])} />
      {label}
    </span>
  );
}
