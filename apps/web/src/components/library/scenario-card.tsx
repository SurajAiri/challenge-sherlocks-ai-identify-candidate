"use client";

import Link from "next/link";
import { ArrowRight, FileWarning, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { ScenarioLibraryEntry } from "@/lib/types";
import { useScenarioLibraryStore } from "@/store/scenario-library-store";

const DIFFICULTY_LABEL: Record<number, string> = {
  1: "Easy",
  2: "Light",
  3: "Moderate",
  4: "Hard",
  5: "Brutal",
};

export function ScenarioCard({ scenario }: { scenario: ScenarioLibraryEntry }) {
  const removeScenario = useScenarioLibraryStore((s) => s.removeScenario);

  return (
    <Card className="group relative overflow-hidden transition-colors hover:border-[var(--accent-signal)]/40">
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="text-base">{scenario.name}</CardTitle>
          {scenario.difficulty != null && (
            <Badge variant="outline" className="shrink-0">
              {DIFFICULTY_LABEL[scenario.difficulty] ?? `Difficulty ${scenario.difficulty}`}
            </Badge>
          )}
        </div>
        <p className="truncate font-mono text-[0.7rem] text-muted-foreground" title={scenario.path}>
          {scenario.path}
        </p>
      </CardHeader>
      <CardContent className="flex-1">
        <p className="line-clamp-3 text-sm text-muted-foreground">
          {scenario.description ?? "No description provided for this scenario."}
        </p>
        {scenario.challengingPoints.length > 0 && (
          <ul className="mt-3 flex flex-wrap gap-1.5">
            {scenario.challengingPoints.slice(0, 3).map((point) => (
              <li key={point}>
                <Badge variant="warning" className="items-start">
                  <FileWarning className="mt-px size-3" />
                  <span className="line-clamp-1 max-w-40">{point}</span>
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
      <CardFooter className="justify-between">
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => removeScenario(scenario.id)}
          aria-label="Remove scenario"
        >
          <Trash2 />
        </Button>
        <Link href={`/session/${scenario.id}`} className={cn(buttonVariants({ size: "sm" }))}>
          Open meeting <ArrowRight className="size-4" />
        </Link>
      </CardFooter>
    </Card>
  );
}
