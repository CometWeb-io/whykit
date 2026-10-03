import { useEffect, useSyncExternalStore } from "react";
import raw from "../generated/vault.json";
// Emitted as a plain JSON file next to the bundle (never inlined, see
// vite.config.ts) and fetched relative to this module, so a sub-path deploy
// and a `connect-src 'self'` policy both hold.
import bodiesUrl from "../generated/bodies.json?url";
import type { VaultIndex } from "../types.ts";
import { createVaultModel } from "./model.ts";

// The bundled index is the summary written by scripts/build-vault-index.mjs:
// everything but the note bodies, which are a separate chunk loaded on demand.
export const model = createVaultModel(raw as VaultIndex);
export const { vault, docs, resolveDoc, linksFor, backlinksFor, canonicalDocs, searchDocs, evidenceFor, docsForEvidence, decisionChain, bodyOf, openCues } = model;

type BodyState = "idle" | "loading" | "ready" | "failed";
let state: BodyState = model.hasBodies() ? "ready" : "idle";
const listeners = new Set<() => void>();
function set(next: BodyState) {
  state = next;
  for (const l of listeners) l();
}

/** Start loading the note bodies (once). Safe to call from anywhere. */
export function loadBodies(): void {
  if (state === "loading" || state === "ready") return;
  set("loading");
  fetch(bodiesUrl)
    .then(res => {
      if (!res.ok) throw new Error(`HTTP ${res.status} for ${res.url}`);
      return res.json() as Promise<Record<string, string>>;
    })
    .then(chunk => {
      model.attachBodies(chunk);
      set("ready");
      // Lowercasing every body for search is the one costly step left; do it
      // while idle rather than inside the next keystroke.
      if (typeof window.requestIdleCallback === "function") window.requestIdleCallback(() => model.prepareSearch(), { timeout: 3000 });
    })
    .catch((err: unknown) => { console.error("WhyKit Explorer: note bodies failed to load", err); set("failed"); });
}

// A deep link to a note or an open search needs the text at once: start the
// download before React renders rather than from the first effect.
if (/(?:^#|&)(?:doc|search)=/.test(location.hash)) loadBodies();

/**
 * The body chunk's state. Pass `eager` to start loading now; otherwise it is
 * fetched once the browser is idle after the first paint.
 */
export function useBodies(eager = false): BodyState {
  const current = useSyncExternalStore(
    cb => { listeners.add(cb); return () => listeners.delete(cb); },
    () => state,
  );
  useEffect(() => {
    if (eager) { loadBodies(); return; }
    if (state !== "idle") return;
    if (typeof window.requestIdleCallback === "function") {
      const handle = window.requestIdleCallback(() => loadBodies(), { timeout: 2000 });
      return () => window.cancelIdleCallback(handle);
    }
    const handle = window.setTimeout(loadBodies, 200);
    return () => window.clearTimeout(handle);
  }, [eager]);
  return current;
}
