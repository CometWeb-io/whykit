import { useEffect, useSyncExternalStore } from "react";
import raw from "../generated/vault.json";
// Emitted as plain JSON files next to the bundle (never inlined, see
// vite.config.ts) and fetched relative to this module, so a sub-path deploy
// and a `connect-src 'self'` policy both hold. A single-file build embeds
// them in the page instead; readChunk prefers that copy.
import bodiesUrl from "../generated/bodies.json?url";
import findingsUrl from "../generated/findings.json?url";
import type { Finding, VaultIndex } from "../types.ts";
import { readChunk } from "./chunks.ts";
import { createVaultModel } from "./model.ts";

// The bundled index is the summary written by scripts/build-vault-index.mjs:
// everything but the note bodies and the lint findings, which are separate
// chunks loaded on demand.
export const model = createVaultModel(raw as VaultIndex);
export const { vault, docs, resolveDoc, linksFor, backlinksFor, canonicalDocs, searchDocs, evidenceFor, docsForEvidence, decisionChain, bodyOf, openCues } = model;
export const findings = (): readonly Finding[] | undefined => model.findings();

export type ChunkState = "idle" | "loading" | "ready" | "failed";

/** One lazily loaded chunk: load it once, and let components follow its state. */
function lazyChunk<T>(name: string, url: string, ready: boolean, attach: (data: T) => void, after?: () => void) {
  let state: ChunkState = ready ? "ready" : "idle";
  const listeners = new Set<() => void>();
  const set = (next: ChunkState) => {
    state = next;
    for (const l of listeners) l();
  };
  function load(): void {
    if (state === "loading" || state === "ready") return;
    set("loading");
    readChunk<T>(name, url)
      .then(data => { attach(data); set("ready"); after?.(); })
      .catch((err: unknown) => { console.error(`WhyKit Explorer: ${name} failed to load`, err); set("failed"); });
  }
  /** The chunk's state. Pass `eager` to start loading now; otherwise it waits until the browser is idle. */
  function use(eager = false): ChunkState {
    const current = useSyncExternalStore(
      cb => { listeners.add(cb); return () => listeners.delete(cb); },
      () => state,
    );
    useEffect(() => {
      if (eager) { load(); return; }
      if (state !== "idle") return;
      if (typeof window.requestIdleCallback === "function") {
        const handle = window.requestIdleCallback(load, { timeout: 2000 });
        return () => window.cancelIdleCallback(handle);
      }
      const handle = window.setTimeout(load, 200);
      return () => window.clearTimeout(handle);
    }, [eager]);
    return current;
  }
  return { load, use };
}

const bodies = lazyChunk<Record<string, string>>("bodies", bodiesUrl, model.hasBodies(), chunk => model.attachBodies(chunk), () => {
  // Lowercasing every body for search is the one costly step left; do it
  // while idle rather than inside the next keystroke.
  if (typeof window.requestIdleCallback === "function") window.requestIdleCallback(() => model.prepareSearch(), { timeout: 3000 });
});
const lint = lazyChunk<Finding[]>("findings", findingsUrl, model.findings() !== undefined, chunk => model.attachFindings(chunk));

/** Start loading the note bodies (once). Safe to call from anywhere. */
export const loadBodies = bodies.load;
/** The note bodies' state; `eager` starts the download now instead of when idle. */
export const useBodies = bodies.use;
/** The lint findings' state. Only Health reads them, so it asks eagerly. */
export const useFindings = (): ChunkState => lint.use(true);

// A deep link needs its chunk at once: start the download before React
// renders rather than from the first effect.
if (/(?:^#|&)(?:doc|search)=/.test(location.hash)) loadBodies();
if (/(?:^#|&)view=health(?:&|$)/.test(location.hash)) lint.load();
