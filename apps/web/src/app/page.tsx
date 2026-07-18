"use client";

import { AddScenarioDialog } from "@/components/library/add-scenario-dialog";
import { EmptyState } from "@/components/library/empty-state";
import { ScenarioCard } from "@/components/library/scenario-card";
import { Logo } from "@/components/logo";
import { useMounted } from "@/lib/use-mounted";
import { useScenarioLibraryStore, useScenarioLibraryHydrated } from "@/store/scenario-library-store";

export default function LibraryPage() {
  const scenarios = useScenarioLibraryStore((s) => s.scenarios);

  // zustand's persist() rehydrates from localStorage asynchronously,
  // on its own schedule - separate from (and slightly later than)
  // React's own hydration. Gating on useMounted() alone isn't enough:
  // there's a real window on a fresh load where mounted is already
  // true but rehydration hasn't resolved yet, during which `scenarios`
  // is still `[]` even if localStorage has entries - which used to
  // flash the "no scenarios yet" EmptyState before flipping to the
  // real list a moment later. useScenarioLibraryHydrated() is the
  // actual "safe to read scenarios now" signal.
  const mounted = useMounted();
  const libraryHydrated = useScenarioLibraryHydrated();
  const ready = mounted && libraryHydrated;

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

      {!ready ? null : scenarios.length === 0 ? (
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
