import type { DecisionRow, EvidenceRow, VaultDoc, VaultIndex } from "../types.ts";
import { extractWikilinks } from "./markdown.ts";

export type VaultModel = ReturnType<typeof createVaultModel>;

/** Open-question headings and "Needs verification" markers: unknowns kept visible. */
export function reviewCues(body: string): number {
  return body.match(/Needs verification|## Open questions/gi)?.length ?? 0;
}

/**
 * Derive every lookup the Explorer needs from the generated index in one pass.
 * Kept free of the bundled JSON import so it can be tested against fixtures.
 */
export function createVaultModel(vault: VaultIndex) {
  const docs = vault.docs;
  // Bodies arrive with the index (tests, older builds) or later as a chunk.
  const bodies = new Map<string, string>();
  for (const d of docs) if (typeof d.body === "string") bodies.set(d.id, d.body);
  let complete = bodies.size === docs.length;
  const byId = new Map(docs.map(d => [d.id.toLowerCase(), d]));
  const aliases = new Map<string, VaultDoc[]>();
  for (const d of docs) {
    for (const key of [d.id.split("/").at(-1) || d.id, ...d.aliases]) {
      const k = key.toLowerCase();
      const list = aliases.get(k);
      if (list) list.push(d);
      else aliases.set(k, [d]);
    }
  }

  function resolveDoc(target: string): VaultDoc | undefined {
    const clean = target.trim().replace(/^\//, "").replace(/\.md$/, "");
    const direct = byId.get(clean.toLowerCase());
    if (direct) return direct;
    const stem = clean.split("/").at(-1)?.toLowerCase() || clean.toLowerCase();
    const hits = aliases.get(stem) || [];
    // An alias shared by several notes is ambiguous; guessing would draw a
    // link the linter rejects, so it stays unresolved.
    return new Set(hits).size === 1 ? hits[0] : undefined;
  }

  // Resolve the graph once; per-query reparsing is quadratic on real vaults.
  const outgoing = new Map<string, VaultDoc[]>();
  const incoming = new Map<string, VaultDoc[]>();
  for (const doc of docs) {
    const targets: VaultDoc[] = [];
    const seen = new Set<string>();
    // A split index carries the links resolved at build time by this same code.
    const resolved = doc.links
      ? doc.links.map(at => docs[at])
      : extractWikilinks(doc.body ?? "").map(resolveDoc);
    for (const target of resolved) {
      if (!target || target.id === doc.id || seen.has(target.id)) continue;
      seen.add(target.id);
      targets.push(target);
      const list = incoming.get(target.id);
      if (list) list.push(doc);
      else incoming.set(target.id, [doc]);
    }
    outgoing.set(doc.id, targets);
  }

  // Built on the first search rather than at start-up: lowercasing every body
  // of a large vault is work the first paint does not need.
  type Entry = { doc: VaultDoc; title: string; id: string; tags: string; summary: string; meta: string; body: string };
  let searchIndex: Entry[] | null = null;
  function entries(): Entry[] {
    if (searchIndex) return searchIndex;
    searchIndex = docs.map(doc => {
      const title = doc.title.toLowerCase();
      const id = doc.id.toLowerCase();
      const tags = doc.tags.join(" ").toLowerCase();
      const summary = doc.summary.toLowerCase();
      const body = (bodies.get(doc.id) ?? "").toLowerCase();
      return { doc, title, id, tags, summary, meta: `${title} ${id} ${tags} ${summary}`, body };
    });
    return searchIndex;
  }

  /** Add the bodies chunk. It must belong to this index: one body per note. */
  function attachBodies(chunk: Readonly<Record<string, string>>): void {
    const missing = docs.filter(d => !Object.hasOwn(chunk, d.id) || typeof chunk[d.id] !== "string");
    if (missing.length || Object.keys(chunk).length !== docs.length) {
      throw new Error(`body chunk does not match this index (${missing.length} notes without a body)`);
    }
    for (const d of docs) bodies.set(d.id, chunk[d.id]!);
    complete = true;
    searchIndex = null;
  }

  const evidenceByKey = new Map(vault.evidence.map(row => [row.id, row]));
  const evidenceUsage = new Map<string, VaultDoc[]>();
  for (const doc of docs) {
    // `citations` covers body mentions too, which is what `whykit trace` counts;
    // an index from an older release only has front-matter source_ids.
    for (const sourceId of new Set([...doc.sourceIds, ...(doc.citations ?? [])])) {
      const list = evidenceUsage.get(sourceId);
      if (list) list.push(doc);
      else evidenceUsage.set(sourceId, [doc]);
    }
  }

  const decisionsById = new Map(vault.decisions.map(row => [row.id, row]));
  const supersededBy = new Map<string, string>();
  for (const row of vault.decisions) {
    if (row.supersedes) supersededBy.set(row.supersedes, row.id);
  }

  /**
   * The full supersession lineage a decision belongs to, oldest first.
   * Walks `supersedes` back to the root and forward through successors, so
   * D-010 yields D-009 → D-010 → D-011. A cycle or a dangling id ends the walk
   * instead of looping; the linter reports those separately.
   */
  function decisionChain(id: string): DecisionRow[] {
    const start = decisionsById.get(id);
    if (!start) return [];
    const seen = new Set([start.id]);
    const back: DecisionRow[] = [];
    let cursor = start;
    while (cursor.supersedes) {
      const prev = decisionsById.get(cursor.supersedes);
      if (!prev || seen.has(prev.id)) break;
      seen.add(prev.id);
      back.unshift(prev);
      cursor = prev;
    }
    const forward: DecisionRow[] = [];
    cursor = start;
    for (;;) {
      const nextId = cursor.supersededBy ?? supersededBy.get(cursor.id);
      const next = nextId ? decisionsById.get(nextId) : undefined;
      if (!next || seen.has(next.id)) break;
      seen.add(next.id);
      forward.push(next);
      cursor = next;
    }
    return [...back, start, ...forward];
  }

  const canonical = docs.filter(d => d.sourceOfTruth && d.status === "approved");

  function searchDocs(q: string, limit = 30): VaultDoc[] {
    const terms = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) return [];
    const hits: { doc: VaultDoc; score: number }[] = [];
    for (const item of entries()) {
      if (!terms.every(term => item.meta.includes(term) || item.body.includes(term))) continue;
      let score = 0;
      for (const term of terms) {
        if (item.title === term) score += 20;
        else if (item.title.startsWith(term)) score += 12;
        else if (item.title.includes(term)) score += 8;
        if (item.id.includes(term)) score += 6;
        if (item.tags.includes(term)) score += 4;
        if (item.summary.includes(term)) score += 2;
        if (item.body.includes(term)) score += 1;
      }
      hits.push({ doc: item.doc, score });
    }
    return hits
      .sort((a, b) => b.score - a.score || a.doc.title.localeCompare(b.doc.title))
      .slice(0, limit)
      .map(item => item.doc);
  }

  return {
    vault,
    docs,
    resolveDoc,
    searchDocs,
    attachBodies,
    /** Build the search index now (e.g. while idle) instead of on the next query. */
    prepareSearch: (): void => { entries(); },
    /** True once every note's body is available (search covers note text). */
    hasBodies: (): boolean => complete,
    bodyOf: (doc: VaultDoc): string | undefined => bodies.get(doc.id),
    /** Review cues across the vault; precomputed in a split index. */
    openCues: (): number => docs.reduce((n, d) => n + (d.cues ?? reviewCues(bodies.get(d.id) ?? "")), 0),
    linksFor: (doc: VaultDoc): VaultDoc[] => outgoing.get(doc.id) || [],
    backlinksFor: (doc: VaultDoc): VaultDoc[] => incoming.get(doc.id) || [],
    canonicalDocs: (): VaultDoc[] => canonical,
    evidenceFor: (id: string): EvidenceRow | undefined => evidenceByKey.get(id),
    docsForEvidence: (id: string): VaultDoc[] => evidenceUsage.get(id) || [],
    decisionChain,
  };
}
