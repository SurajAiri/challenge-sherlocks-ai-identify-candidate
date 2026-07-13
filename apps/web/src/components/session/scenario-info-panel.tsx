"use client";

import { CalendarClock, Info } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { ScenarioLibraryEntry } from "@/lib/types";
import { useSessionStore } from "@/store/session-store";

/**
 * Shows what the External Metadata block of the challenge describes:
 * candidate name/email, calendar invite, interview schedule,
 * interviewer names - all from the `context` SSE frame, which is the
 * same information a real Engine would have going in. Ground truth
 * (`groundTruthParticipantId`) is deliberately never rendered here -
 * it only shows up on the post-run results page, for scoring.
 */
export function ScenarioInfoPanel({ scenario }: { scenario: ScenarioLibraryEntry }) {
  const context = useSessionStore((s) => s.context);

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Info className="size-4 text-muted-foreground" />
        Scenario
      </div>

      <div>
        <p className="text-sm font-medium text-foreground">{scenario.name}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{scenario.description}</p>
      </div>

      {context ? (
        <dl className="flex flex-col gap-2 border-t border-border pt-3 text-xs">
          <div>
            <dt className="text-muted-foreground">Candidate (per calendar invite)</dt>
            <dd className="font-medium text-foreground">{context.candidate_name}</dd>
            <dd className="text-muted-foreground">{context.candidate_email}</dd>
          </div>
          {context.interviewer_names.length > 0 && (
            <div>
              <dt className="text-muted-foreground">Interviewers</dt>
              <dd className="flex flex-wrap gap-1 pt-1">
                {context.interviewer_names.map((n) => (
                  <Badge key={n} variant="outline">
                    {n}
                  </Badge>
                ))}
              </dd>
            </div>
          )}
          {typeof context.calendar_invite?.title === "string" && (
            <div className="flex items-start gap-1.5">
              <CalendarClock className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
              <span className="text-muted-foreground">{context.calendar_invite.title as string}</span>
            </div>
          )}
        </dl>
      ) : (
        <p className="border-t border-border pt-3 text-xs text-muted-foreground">
          Calendar/context details arrive as soon as the run starts.
        </p>
      )}
    </div>
  );
}
