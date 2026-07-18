"use client";

import { useRef, useState } from "react";
import { FolderOpen, FolderPlus, Loader2, UploadCloud } from "lucide-react";

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

// ── Module-level serial evaluation queue ────────────────────────────────────
// pyttsx3 (used by the simulator for TTS) runs a global event loop that cannot
// handle concurrent calls. Firing all /evaluation requests at once crashes it
// with "run loop already started". This queue ensures only one request is
// in-flight at a time, regardless of how many scenarios are being imported.
const evalQueue: Array<() => Promise<void>> = [];
let evalQueueRunning = false;

function enqueueEval(task: () => Promise<void>) {
  evalQueue.push(task);
  if (!evalQueueRunning) drainEvalQueue();
}

async function drainEvalQueue() {
  evalQueueRunning = true;
  while (evalQueue.length > 0) {
    const task = evalQueue.shift()!;
    await task();
  }
  evalQueueRunning = false;
}
// ─────────────────────────────────────────────────────────────────────────────

export function AddScenarioDialog() {
  const [open, setOpen] = useState(false);
  const [path, setPath] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const dropZoneRef = useRef<HTMLDivElement>(null);
  const addScenario = useScenarioLibraryStore((s) => s.addScenario);
  const updateScenario = useScenarioLibraryStore((s) => s.updateScenario);

  function reset() {
    setPath("");
    setError(null);
  }

  // ── Resolve folder name → absolute path via browse API ──────────────────

  async function resolveByName(folderName: string) {
    setResolving(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/simulator/browse?name=${encodeURIComponent(folderName)}`
      );
      const json = await res.json();
      if (!res.ok) {
        setError(typeof json?.detail === "string" ? json.detail : "Could not resolve the folder path.");
        return;
      }
      setPath(json.path as string);
    } catch {
      setError("Could not reach the server to resolve the folder path.");
    } finally {
      setResolving(false);
    }
  }

  // ── Drag & drop ──────────────────────────────────────────────────────────

  function handleDragOver(e: React.DragEvent) {
    e.preventDefault();
    setIsDragging(true);
  }

  function handleDragLeave(e: React.DragEvent) {
    e.preventDefault();
    setIsDragging(false);
  }

  async function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setIsDragging(false);
    const entry = e.dataTransfer.items[0]?.webkitGetAsEntry?.();
    if (!entry) { setError("Could not read the dropped item. Try dropping a folder."); return; }
    if (!entry.isDirectory) { setError("Please drop a folder, not a file."); return; }
    await resolveByName(entry.name);
  }

  // ── Browse via File System Access API ───────────────────────────────────

  async function handleBrowse() {
    if (!("showDirectoryPicker" in window)) {
      setError("Your browser doesn't support the directory picker. Please drag & drop or paste the path.");
      return;
    }
    try {
      // @ts-expect-error – File System Access API not in lib.dom yet
      const handle = await window.showDirectoryPicker({ mode: "read" });
      await resolveByName(handle.name as string);
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return;
      setError("Could not open the folder picker.");
    }
  }

  // ── Background evaluation for one scenario ───────────────────────────────
  // Runs entirely outside of any dialog state — dialog is already closed.

  // Returns a Promise so it can be serialised through the eval queue.
  function evalInBackground(scenarioPath: string, id: string): Promise<void> {
    const controller = new AbortController();
    // 3-minute timeout per scenario (TTS/ffmpeg synthesis can be slow)
    const timer = setTimeout(() => controller.abort(), 3 * 60 * 1000);

    return fetch("/api/simulator/evaluation", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ scenario_dir: scenarioPath }),
      signal: controller.signal,
    })
      .then(async (res) => {
        const json = await res.json();
        if (!res.ok) {
          const reason =
            typeof json?.detail === "string"
              ? json.detail
              : (json?.detail?.errors?.join?.(", ") ?? `Simulator returned ${res.status}`);
          updateScenario(id, { status: "error", importError: reason });
          return;
        }
        const parsed = evaluationResponseSchema.parse(json);
        updateScenario(id, {
          status: "ready",
          name: parsed.name,
          slug: parsed.slug,
          description: parsed.description ?? null,
          difficulty: parsed.difficulty ?? null,
          challengingPoints: parsed.challenging_points,
          expectedEvidence: parsed.expected_evidence,
          groundTruthParticipantId: parsed.ground_truth_participant_id ?? null,
          importError: undefined,
        });
      })
      .catch((err) => {
        const isTimeout = err instanceof Error && err.name === "AbortError";
        updateScenario(id, {
          status: "error",
          importError: isTimeout
            ? "Timed out — the simulator is taking too long. Is it running?"
            : err instanceof Error
              ? err.message
              : "Unexpected error during evaluation.",
        });
      })
      .finally(() => clearTimeout(timer));
  }

  // ── Submit ───────────────────────────────────────────────────────────────

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = path.trim();
    if (!trimmed) return;

    setSubmitting(true);
    setError(null);

    // Step 1: expand — fast filesystem check, no simulator involved.
    let scenarioPaths: string[];
    try {
      const res = await fetch(`/api/simulator/expand?dir=${encodeURIComponent(trimmed)}`);
      const json = await res.json();
      if (!res.ok) {
        setError(typeof json?.detail === "string" ? json.detail : "Could not determine scenario paths.");
        setSubmitting(false);
        return;
      }
      scenarioPaths = json.paths as string[];
    } catch {
      setError("Could not reach the server.");
      setSubmitting(false);
      return;
    }

    // Step 2: add each path to the library immediately as "pending".
    // Dedup is handled by the store (same path = returns existing entry).
    const entries = scenarioPaths.map((p) =>
      addScenario({
        path: p,
        // Use the folder name as a placeholder until eval completes
        name: p.split("/").pop() ?? p,
        slug: "",
        status: "pending",
        challengingPoints: [],
        expectedEvidence: {},
      })
    );

    // Step 3: close the dialog immediately — user is unblocked.
    reset();
    setOpen(false);
    setSubmitting(false);

    // Step 4: enqueue background evaluation for each newly-added "pending" entry.
    // enqueueEval serialises all calls — only one /evaluation request is ever
    // in-flight at a time, preventing pyttsx3's "run loop already started" crash.
    for (const entry of entries) {
      if (entry.status === "pending") {
        enqueueEval(() => evalInBackground(entry.path, entry.id));
      }
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
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
            Point at a scenario directory (containing <code>index.yml</code>)
            or a parent folder — all valid sub-folders will be added automatically.
            Scenarios appear in the library immediately while the simulator
            compiles them in the background.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          {/* ── Drop zone + browse button ──────────────────────────────── */}
          <div
            ref={dropZoneRef}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            className={[
              "flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-6 text-center transition-colors select-none",
              isDragging
                ? "border-primary bg-primary/5 text-primary"
                : "border-border text-muted-foreground hover:border-primary/50",
            ].join(" ")}
          >
            <UploadCloud className="size-7 opacity-60" />
            <p className="text-sm">Drag &amp; drop a scenario folder or parent folder here</p>
            <p className="text-xs opacity-60">or</p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={resolving}
              onClick={handleBrowse}
            >
              {resolving ? <Loader2 className="size-3.5 animate-spin" /> : <FolderOpen className="size-3.5" />}
              {resolving ? "Resolving…" : "Browse folder"}
            </Button>
          </div>

          {/* ── Path input ────────────────────────────────────────────── */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="scenario-dir">Directory path</Label>
            <Input
              id="scenario-dir"
              placeholder="/absolute/path/to/scenarios"
              value={path}
              onChange={(e) => { setPath(e.target.value); setError(null); }}
              spellCheck={false}
            />
          </div>

          {error && (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </p>
          )}

          <DialogFooter>
            <DialogClose render={<Button type="button" variant="ghost" />}>
              Cancel
            </DialogClose>
            <Button type="submit" disabled={submitting || resolving || !path.trim()}>
              {submitting && <Loader2 className="animate-spin" />}
              {submitting ? "Adding…" : "Add scenario"}
            </Button>
          </DialogFooter>
        </form>
      </DialogPopup>
    </Dialog>
  );
}
