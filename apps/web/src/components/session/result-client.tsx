"use client";

import Link from "next/link";
import { CheckCircle2, HelpCircle, RotateCcw, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useMounted } from "@/lib/use-mounted";
import { cn } from "@/lib/utils";
import { useScenarioLibraryStore, useScenarioLibraryHydrated } from "@/store/scenario-library-store";
import { useSessionStore } from "@/store/session-store";

/**
 * Ground truth (`groundTruthParticipantId`) is read here from the
 * locally-stored library entry - which came from the simulator's
 * author/scoring-only `/evaluation` endpoint (see
 * `add-scenario-dialog.tsx`) - purely to score the run after the fact.
 * It's never fed into `session-store`'s live event handling or sent to
 * the Engine at any point, so scoring here can't leak into the
 * identification path it's meant to be judging.
 */
export function ResultClient({ scenarioId }: { scenarioId: string }) {
  const scenario = useScenarioLibraryStore((s) => s.getById(scenarioId));
  const engineLatest = useSessionStore((s) => s.engineLatest);
  const participants = useSessionStore((s) => s.participants);
  const transcript = useSessionStore((s) => s.transcript);
  const context = useSessionStore((s) => s.context);
  const rawLog = useSessionStore((s) => s.rawLog);

  const mounted = useMounted();
  // See useScenarioLibraryHydrated's doc comment - useMounted() alone
  // doesn't guarantee the persisted library has actually been read
  // yet on a fresh page load, so without this a direct/refreshed hit
  // on the results page could show "not in your local library" for a
  // beat even when the scenario is right there in localStorage.
  const libraryHydrated = useScenarioLibraryHydrated();
  if (!mounted || !libraryHydrated) return null;

  if (!scenario) {
    return (
      <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
        <p className="text-sm text-muted-foreground">This scenario isn&apos;t in your local library.</p>
        <Link href="/" className={cn(buttonVariants({ size: "sm" }))}>
          Back to library
        </Link>
      </div>
    );
  }

  const predictedId = engineLatest?.candidateParticipantId ?? null;
  const possibleIds = engineLatest?.possibleCandidateIds ?? [];
  const groundTruthId = scenario.groundTruthParticipantId ?? null;
  const predictedName = predictedId
    ? (participants[predictedId]?.displayName ?? predictedId)
    : possibleIds.length > 1
      ? possibleIds.map((pid) => participants[pid]?.displayName ?? pid).join(" / ")
      : null;
  const groundTruthName = groundTruthId ? participants[groundTruthId]?.displayName ?? groundTruthId : null;

  // Scored against the whole possible-candidate set: naming the real
  // candidate inside an ambiguous pair is still a pass.
  const verdict: "correct" | "incorrect" | "unknown" =
    !engineLatest || !groundTruthId ? "unknown" : possibleIds.includes(groundTruthId) ? "correct" : "incorrect";

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 px-6 py-10">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-xs text-muted-foreground">{scenario.name}</p>
          <h1 className="text-2xl font-semibold tracking-tight">Session results</h1>
        </div>
        <Link href="/" className={cn(buttonVariants({ variant: "outline", size: "sm" }))}>
          <RotateCcw className="size-3.5" /> Run another scenario
        </Link>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Engine verdict</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {!engineLatest ? (
            <p className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
              The Engine hasn&apos;t sent a prediction for this run yet — this page will fill in as
              soon as it does. Nothing to compare against right now.
            </p>
          ) : (
            <div className="flex items-center gap-3 rounded-lg border border-border p-4">
              <VerdictIcon verdict={verdict} />
              <div>
                <p className="text-sm font-medium text-foreground">
                  Predicted: {predictedName ?? <span className="text-muted-foreground">no candidate identified</span>}
                </p>
                {engineLatest.confidence != null && (
                  <p className="text-xs text-muted-foreground">
                    Confidence: {Math.round(engineLatest.confidence * 100)}%
                  </p>
                )}
                {engineLatest.reasoning && (
                  <p className="mt-1 text-xs text-muted-foreground">{engineLatest.reasoning}</p>
                )}
              </div>
            </div>
          )}

          <div className="flex flex-wrap gap-2 text-xs">
            <Badge variant="outline">Ground truth: {groundTruthName ?? "not recorded for this scenario"}</Badge>
            {scenario.difficulty != null && <Badge variant="outline">Difficulty {scenario.difficulty}/5</Badge>}
          </div>
        </CardContent>
      </Card>

      {scenario.challengingPoints.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>What this scenario was designed to stress</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="flex flex-col gap-1.5 text-sm text-muted-foreground">
              {scenario.challengingPoints.map((point) => (
                <li key={point} className="flex items-start gap-2">
                  <span className="mt-1.5 size-1 shrink-0 rounded-full bg-muted-foreground" />
                  {point}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Run summary</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
          <Stat label="Participants" value={Object.keys(participants).length} />
          <Stat label="Transcript segments" value={transcript.length} />
          <Stat label="Events logged" value={rawLog.length} />
          <Stat label="Interviewers" value={context?.interviewer_names.length ?? 0} />
        </CardContent>
      </Card>
    </div>
  );
}

function VerdictIcon({ verdict }: { verdict: "correct" | "incorrect" | "unknown" }) {
  if (verdict === "correct") return <CheckCircle2 className="size-6 shrink-0 text-emerald-400" />;
  if (verdict === "incorrect") return <XCircle className="size-6 shrink-0 text-destructive" />;
  return <HelpCircle className="size-6 shrink-0 text-muted-foreground" />;
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <p className="text-lg font-semibold text-foreground">{value}</p>
      <p className="text-xs text-muted-foreground">{label}</p>
    </div>
  );
}
