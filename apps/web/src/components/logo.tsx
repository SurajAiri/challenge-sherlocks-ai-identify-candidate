import { cn } from "@/lib/utils";

/** A magnifier-over-waveform mark: fraud detection reading a signal.
 * Deliberately not a generic "AI sparkle" - it's meant to look like an
 * instrument, matching the "engine reading evidence" framing. */
export function Logo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      className={cn("size-5 text-[var(--accent-signal)]", className)}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <circle cx="13.5" cy="13.5" r="9" stroke="currentColor" strokeWidth="2.2" />
      <path
        d="M9.5 13.5h2l1.5-4 2 8 1.5-4h2.5"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <line
        x1="20.2"
        y1="20.2"
        x2="27"
        y2="27"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
      />
    </svg>
  );
}
