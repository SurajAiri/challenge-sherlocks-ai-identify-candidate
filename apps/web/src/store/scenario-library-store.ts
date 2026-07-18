"use client";

import { useEffect, useState } from "react";
import { create } from "zustand";
import { persist } from "zustand/middleware";
import { makeId } from "@/lib/id";
import type { ScenarioLibraryEntry } from "@/lib/types";

interface ScenarioLibraryState {
  scenarios: ScenarioLibraryEntry[];
  /** Adds a scenario, deduped by absolute path. Returns the existing
   * entry if the path is already present instead of adding a dupe. */
  addScenario: (entry: Omit<ScenarioLibraryEntry, "id" | "addedAt">) => ScenarioLibraryEntry;
  /** Patches an existing scenario entry by id. Used to transition
   * a "pending" entry to "ready" or "error" after background evaluation. */
  updateScenario: (id: string, patch: Partial<Omit<ScenarioLibraryEntry, "id" | "addedAt">>) => void;
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

      updateScenario: (id, patch) =>
        set((state) => ({
          scenarios: state.scenarios.map((s) =>
            s.id === id ? { ...s, ...patch } : s
          ),
        })),

      removeScenario: (id) =>
        set((state) => ({ scenarios: state.scenarios.filter((s) => s.id !== id) })),

      getById: (id) => get().scenarios.find((s) => s.id === id),
    }),
    { name: "sherlock:scenario-library" }
  )
);

/**
 * Tracks whether this store's localStorage rehydration has actually
 * finished - NOT the same thing as "React has hydrated"
 * (useMounted/useSyncExternalStore). zustand's `persist` middleware
 * reads localStorage and applies it to the store on a microtask after
 * store creation (so SSR and CSR behave uniformly) - there's a real,
 * if brief, window after module init where `scenarios` is still `[]`
 * even though a browser reload's `useMounted()` is already `true`.
 *
 * SessionClient used to gate only on useMounted(), so during that
 * window `getById(scenarioId)` returned undefined, rendered the "not
 * in your local library" bailout, and - critically - skipped the
 * effect that constructs the EngineSocket entirely. The page looked
 * broken until a *second* reload happened to land after rehydration
 * resolved, which is exactly the "only works after I reload once"
 * symptom.
 *
 * Deliberately does NOT reach for `useScenarioLibraryStore.persist`
 * (zustand's documented persist-middleware introspection API) - in
 * this app's actual build that property comes back undefined
 * (bundler/SSR interaction we don't have full visibility into from
 * here), which crashed every caller with "Cannot read properties of
 * undefined (reading 'hasHydrated')". `store.subscribe` and
 * `store.getState`, by contrast, are core zustand APIs present on
 * every store regardless of middleware, so they can't go missing the
 * same way - using those instead makes this robust to whatever is
 * causing `.persist` to disappear here.
 *
 * Rehydration is detected the same way regardless of what actually
 * caused a state change: as soon as the store's state object changes
 * *at all* after this hook mounts (whether that's persist applying
 * saved data, or genuinely nothing being saved), we know the
 * synchronous/microtask rehydration step has had its chance to run.
 * A microtask + rAF defer covers the case where rehydration completes
 * with an *empty* saved library (no state change to observe at all).
 */
export function useScenarioLibraryHydrated(): boolean {
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    let settled = false;
    const markHydrated = () => {
      if (settled) return;
      settled = true;
      setHydrated(true);
    };

    // Fires immediately if persist's rehydration (or any other store
    // update) lands after this effect mounts.
    const unsubscribe = useScenarioLibraryStore.subscribe(markHydrated);

    // Covers the "saved library was empty, so rehydrating produced no
    // observable state change" case: defer past the microtask queue
    // (where persist's own hydration promise resolves) and one more
    // frame for safety, then consider it settled either way.
    const raf = requestAnimationFrame(() => {
      Promise.resolve().then(markHydrated);
    });

    return () => {
      unsubscribe();
      cancelAnimationFrame(raf);
    };
  }, []);

  return hydrated;
}
