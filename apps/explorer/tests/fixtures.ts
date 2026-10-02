import type { DocStatus, VaultDoc, VaultIndex } from "../src/types.ts";

export function doc(id: string, over: Partial<VaultDoc> & { status?: DocStatus } = {}): VaultDoc {
  return {
    id,
    title: id.split("/").at(-1) || id,
    aliases: [],
    type: "guide",
    status: "approved",
    owner: "Owner",
    created: "2026-01-01",
    lastUpdated: "2026-01-01",
    reviewBy: "",
    sourceOfTruth: false,
    sensitivity: "internal",
    sourceIds: [],
    tags: [],
    workstream: id.includes("/") ? id.split("/")[0]! : "root",
    decisionId: null,
    supersedes: null,
    summary: "",
    body: "",
    ...over,
  };
}

export function index(docs: VaultDoc[], over: Partial<VaultIndex> = {}): VaultIndex {
  return {
    generatedAt: "2026-09-17T00:00:00Z",
    vaultName: "Example vault",
    docs,
    evidence: [],
    decisions: [],
    reviews: [],
    lint: { files: docs.length, errors: 0, warnings: 0, findings: [] },
    ...over,
  };
}
