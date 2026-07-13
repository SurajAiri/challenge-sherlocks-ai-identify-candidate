"use client";

import { useEffect, useState } from "react";

import { AddScenarioDialog } from "@/components/library/add-scenario-dialog";
import { EmptyState } from "@/components/library/empty-state";
import { ScenarioCard } from "@/components/library/scenario-card";
import { Logo } from "@/components/logo";
import { useScenarioLibraryStore } from "@/store/scenario-library-store";

export default function LibraryPage() {
  const scenarios = useScenarioLibraryStore((s) => s.scenarios);

  // zustand's persist() rehydrates from localStorage after first paint -
  // rendering the list before that would mismatch SSR output, so hold
  // off on real content for a tick.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-8 px-6 py-10">
      <header className="flex flex-col gap-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Logo />
          <span className="font-medium text-foreground">Sherlock</span>
          <span>/ candidate identifier — testing dashboard</span>
        </div>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold tracking-tight">Scenario library</h1>
            <p className="max-w-xl text-sm text-muted-foreground">
              Load a scenario, watch the simulated meeting come in, and check the Engine&apos;s
              live confidence once it&apos;s wired up.
            </p>
          </div>
          {scenarios.length > 0 && <AddScenarioDialog />}
        </div>
      </header>

      {!mounted ? null : scenarios.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {scenarios.map((scenario) => (
            <ScenarioCard key={scenario.id} scenario={scenario} />
          ))}
        </div>
      )}
    </div>
  );
}
