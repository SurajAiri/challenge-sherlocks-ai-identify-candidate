"use client";

import { useState } from "react";
import { FolderPlus, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogPopup,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { evaluationResponseSchema } from "@/lib/types";
import { useScenarioLibraryStore } from "@/store/scenario-library-store";

export function AddScenarioDialog() {
  const [open, setOpen] = useState(false);
  const [path, setPath] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const addScenario = useScenarioLibraryStore((s) => s.addScenario);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = path.trim();
    if (!trimmed) return;

    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/simulator/evaluation", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ scenario_dir: trimmed }),
      });
      const json = await res.json();

      if (!res.ok) {
        setError(
          typeof json?.detail === "string"
            ? json.detail
            : json?.detail?.errors?.join?.(", ") ??
                json?.detail ??
                `The simulator couldn't load that scenario (${res.status}).`
        );
        return;
      }

      const parsed = evaluationResponseSchema.parse(json);
      addScenario({
        path: trimmed,
        name: parsed.name,
        slug: parsed.slug,
        description: parsed.description ?? null,
        difficulty: parsed.difficulty ?? null,
        challengingPoints: parsed.challenging_points,
        expectedEvidence: parsed.expected_evidence,
        groundTruthParticipantId: parsed.ground_truth_participant_id ?? null,
      });
      setPath("");
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong reaching the simulator.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button>
            <FolderPlus /> Add scenario
          </Button>
        }
      />
      <DialogPopup>
        <DialogHeader>
          <DialogTitle>Add a scenario</DialogTitle>
          <DialogDescription>
            Point at a scenario directory (the folder containing its <code>index.yml</code>). The
            simulator validates and compiles it before it&apos;s added — already-added paths are
            reused instead of duplicated.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="scenario-dir">Scenario directory</Label>
            <Input
              id="scenario-dir"
              placeholder="apps/simulator/scenarios-ref/demo_clean"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              autoFocus
              spellCheck={false}
            />
          </div>
          {error && (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <DialogClose render={<Button type="button" variant="ghost" />}>Cancel</DialogClose>
            <Button type="submit" disabled={loading || !path.trim()}>
              {loading && <Loader2 className="animate-spin" />}
              {loading ? "Validating…" : "Add scenario"}
            </Button>
          </DialogFooter>
        </form>
      </DialogPopup>
    </Dialog>
  );
}
