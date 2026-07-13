"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";
import { makeId } from "@/lib/id";
import type { ScenarioLibraryEntry } from "@/lib/types";

interface ScenarioLibraryState {
  scenarios: ScenarioLibraryEntry[];
  /** Adds a scenario, deduped by absolute path. Returns the existing
   * entry if the path is already present instead of adding a dupe. */
  addScenario: (entry: Omit<ScenarioLibraryEntry, "id" | "addedAt">) => ScenarioLibraryEntry;
  removeScenario: (id: string) => void;
  getById: (id: string) => ScenarioLibraryEntry | undefined;
}

export const useScenarioLibraryStore = create<ScenarioLibraryState>()(
  persist(
    (set, get) => ({
      scenarios: [],

      addScenario: (entry) => {
        const existing = get().scenarios.find((s) => s.path === entry.path);
        if (existing) return existing;

        const created: ScenarioLibraryEntry = {
          ...entry,
          id: makeId("scenario"),
          addedAt: Date.now(),
        };
        set((state) => ({ scenarios: [created, ...state.scenarios] }));
        return created;
      },

      removeScenario: (id) =>
        set((state) => ({ scenarios: state.scenarios.filter((s) => s.id !== id) })),

      getById: (id) => get().scenarios.find((s) => s.id === id),
    }),
    { name: "sherlock:scenario-library" }
  )
);
