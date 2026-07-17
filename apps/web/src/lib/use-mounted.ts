"use client";

import { useSyncExternalStore } from "react";

const subscribe = () => () => {};

/**
 * Returns `false` during SSR and the first client render, `true` after
 * hydration. Use it to gate rendering of content that depends on
 * client-only state (e.g. localStorage-persisted stores) so the
 * server-rendered HTML and the first client render match.
 */
export function useMounted(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
}
