import type { Finding, VaultIndex } from "../types.ts";
import { createVaultModel, reviewCues } from "./model.ts";

export { reviewCues };

/**
 * Split a full Explorer index into what the first paint needs and the note
 * bodies, which can load afterwards.
 *
 * Bodies are most of a large vault's index (about two thirds at 5,000 notes)
 * but only the document view and full-text search read them. The summary
 * keeps everything else and adds what used to be derived from the bodies at
 * start-up: each note's resolved links, computed here by the same resolver
 * the browser runs, and its count of review cues.
 *
 * Lint findings are split off the same way (about half a megabyte at 5,000
 * notes, read only by Health); the summary keeps their counts.
 */
export function splitIndex(full: VaultIndex): { summary: VaultIndex; bodies: Record<string, string>; findings: Finding[] } {
  const model = createVaultModel(full);
  const position = new Map(full.docs.map((d, i) => [d.id, i]));
  const entries: [string, string][] = [];
  const docs = full.docs.map(d => {
    const { body = "", ...rest } = d;
    entries.push([d.id, body]);
    return { ...rest, links: model.linksFor(d).map(target => position.get(target.id)!), cues: reviewCues(body) };
  });
  const { findings = [], ...counts } = full.lint;
  return { summary: { ...full, docs, lint: counts }, bodies: Object.fromEntries(entries), findings };
}
