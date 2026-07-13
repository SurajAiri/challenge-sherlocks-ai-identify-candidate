import { SearchCode } from "lucide-react";

import { AddScenarioDialog } from "@/components/library/add-scenario-dialog";

export function EmptyState() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 rounded-xl border border-dashed border-border py-24 text-center">
      <SearchCode className="size-8 text-muted-foreground" />
      <div className="flex flex-col gap-1">
        <p className="font-medium text-foreground">No scenarios yet</p>
        <p className="max-w-sm text-sm text-muted-foreground">
          Add a scenario directory from the simulator (e.g.{" "}
          <code className="rounded bg-muted px-1 py-0.5 text-xs">
            apps/simulator/scenarios-ref/demo_clean
          </code>
          ) to preview its meeting and start a run.
        </p>
      </div>
      <AddScenarioDialog />
    </div>
  );
}
