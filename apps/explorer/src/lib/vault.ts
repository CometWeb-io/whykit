import raw from "../generated/vault.json";
import type { EvidenceRow, VaultDoc, VaultIndex } from "../types";
import { extractWikilinks } from "./markdown";

export const vault = raw as VaultIndex;
export const docs = vault.docs;
const byId = new Map(docs.map(d => [d.id.toLowerCase(), d]));
const aliases = new Map<string, VaultDoc[]>();
for (const d of docs) {
  for (const key of [d.id.split("/").at(-1) || d.id, ...d.aliases]) {
    const k = key.toLowerCase();
    aliases.set(k, [...(aliases.get(k) || []), d]);
  }
}

export function resolveDoc(target: string): VaultDoc | undefined {
  const clean = target.replace(/^\//, "").replace(/\.md$/, "");
  const direct = byId.get(clean.toLowerCase());
  if (direct) return direct;
  const stem = clean.split("/").at(-1)?.toLowerCase() || clean.toLowerCase();
  const hits = aliases.get(stem) || [];
  return hits.length === 1 ? hits[0] : undefined;
}

// Resolve the graph once. The old implementation reparsed every document for
// every backlinks query, which becomes quadratic on real vaults.
const outgoing = new Map<string, VaultDoc[]>();
const incoming = new Map<string, VaultDoc[]>();
for (const doc of docs) {
  const targets: VaultDoc[] = [];
  const seen = new Set<string>();
  for (const rawTarget of extractWikilinks(doc.body)) {
    const target = resolveDoc(rawTarget);
    if (!target || seen.has(target.id)) continue;
    seen.add(target.id);
    targets.push(target);
    incoming.set(target.id, [...(incoming.get(target.id) || []), doc]);
  }
  outgoing.set(doc.id, targets);
}

const searchIndex = docs.map(doc => ({
  doc,
  title: doc.title.toLowerCase(),
  id: doc.id.toLowerCase(),
  tags: doc.tags.join(" ").toLowerCase(),
  summary: doc.summary.toLowerCase(),
  body: doc.body.toLowerCase(),
}));

const evidenceByKey = new Map(vault.evidence.map(row => [row.id, row]));
const evidenceUsage = new Map<string, VaultDoc[]>();
for (const doc of docs) {
  for (const sourceId of doc.sourceIds) {
    evidenceUsage.set(sourceId, [...(evidenceUsage.get(sourceId) || []), doc]);
  }
}

export function linksFor(doc: VaultDoc) { return outgoing.get(doc.id) || []; }
export function backlinksFor(doc: VaultDoc) { return incoming.get(doc.id) || []; }
export function canonicalDocs() { return docs.filter(d => d.sourceOfTruth && d.status === "approved"); }
export function evidenceFor(id: string): EvidenceRow | undefined { return evidenceByKey.get(id); }
export function docsForEvidence(id: string): VaultDoc[] { return evidenceUsage.get(id) || []; }

export function searchDocs(q: string) {
  const terms = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return [];
  return searchIndex
    .map(item => {
      const haystack = `${item.title} ${item.id} ${item.tags} ${item.summary} ${item.body}`;
      if (!terms.every(term => haystack.includes(term))) return null;
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
      return { doc: item.doc, score };
    })
    .filter((item): item is { doc: VaultDoc; score: number } => Boolean(item))
    .sort((a, b) => b.score - a.score || a.doc.title.localeCompare(b.doc.title))
    .slice(0, 30)
    .map(item => item.doc);
}
