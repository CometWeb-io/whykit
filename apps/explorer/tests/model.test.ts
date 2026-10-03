import { test } from "node:test";
import assert from "node:assert/strict";
import { createVaultModel } from "../src/lib/model.ts";
import type { DecisionRow } from "../src/types.ts";
import { doc, index } from "./fixtures.ts";

test("resolves ids, stems and aliases case-insensitively", () => {
  const m = createVaultModel(index([
    doc("00-context/company", { aliases: ["Org"] }),
    doc("06-decisions/D-001"),
  ]));
  assert.equal(m.resolveDoc("00-context/company.md")?.id, "00-context/company");
  assert.equal(m.resolveDoc("/00-context/Company")?.id, "00-context/company");
  assert.equal(m.resolveDoc("company")?.id, "00-context/company");
  assert.equal(m.resolveDoc("org")?.id, "00-context/company");
  assert.equal(m.resolveDoc("missing"), undefined);
});

test("ambiguous stems stay unresolved", () => {
  const m = createVaultModel(index([doc("a/README"), doc("b/README")]));
  assert.equal(m.resolveDoc("README"), undefined);
  assert.equal(m.resolveDoc("a/README")?.id, "a/README");
});

test("a note whose id and alias collide is not ambiguous with itself", () => {
  const m = createVaultModel(index([doc("x/plan", { aliases: ["plan"] })]));
  assert.equal(m.resolveDoc("plan")?.id, "x/plan");
});

test("links and backlinks are deduplicated and ignore self-links", () => {
  const m = createVaultModel(index([
    doc("a", { body: "[[b]] [[b|again]] [[a]] [[missing]]" }),
    doc("b", { body: "[[a]]" }),
  ]));
  const a = m.resolveDoc("a")!, b = m.resolveDoc("b")!;
  assert.deepEqual(m.linksFor(a).map(d => d.id), ["b"]);
  assert.deepEqual(m.backlinksFor(a).map(d => d.id), ["b"]);
  assert.deepEqual(m.backlinksFor(b).map(d => d.id), ["a"]);
});

test("evidence usage counts each citing document once", () => {
  const m = createVaultModel(index(
    [doc("a", { sourceIds: ["E-001", "E-001"] }), doc("b", { sourceIds: ["E-001"] })],
    { evidence: [{ id: "E-001", state: "active", source: "s", type: "t", date: "", accessed: "", location: "", claims: "" }] },
  ));
  assert.deepEqual(m.docsForEvidence("E-001").map(d => d.id), ["a", "b"]);
  assert.equal(m.evidenceFor("E-001")?.source, "s");
  assert.deepEqual(m.docsForEvidence("E-404"), []);
});

test("search requires every term and ranks title hits first", () => {
  const m = createVaultModel(index([
    doc("x/body-only", { title: "Other", body: "pricing review notes" }),
    doc("x/titled", { title: "Pricing review", body: "" }),
    doc("x/partial", { title: "Pricing", body: "" }),
  ]));
  assert.deepEqual(m.searchDocs("pricing review").map(d => d.id), ["x/titled", "x/body-only"]);
  assert.deepEqual(m.searchDocs("   "), []);
  assert.equal(m.searchDocs("pricing", 1).length, 1);
});

test("canonical docs must be approved sources of truth", () => {
  const m = createVaultModel(index([
    doc("a", { sourceOfTruth: true }),
    doc("b", { sourceOfTruth: true, status: "draft" }),
    doc("c"),
  ]));
  assert.deepEqual(m.canonicalDocs().map(d => d.id), ["a"]);
});

test("indexing a large vault stays fast", () => {
  const docs = Array.from({ length: 5000 }, (_, i) => doc(`w${i % 20}/n${i}`, {
    body: `[[n${(i + 1) % 5000}]] [[n${(i + 7) % 5000}]] some body text ${i}`,
  }));
  const start = performance.now();
  const m = createVaultModel(index(docs));
  m.searchDocs("body 4999");
  const elapsed = performance.now() - start;
  assert.equal(m.linksFor(docs[0]!).length, 2);
  assert.equal(m.backlinksFor(docs[1]!).length, 2);
  assert.ok(elapsed < 2000, `indexing took ${elapsed.toFixed(0)} ms`);
});

function decision(id: string, over: Partial<DecisionRow> = {}): DecisionRow {
  return { id, title: id, date: "2026-01-01", owner: "Owner", status: "accepted", recordId: `06-decisions/${id}`, supersedes: null, ...over };
}

test("decision chains walk back to the root and forward to the latest successor", () => {
  const m = createVaultModel(index([], { decisions: [
    decision("D-001"),
    decision("D-009", { status: "superseded", supersededBy: "D-010" }),
    decision("D-010", { status: "superseded", supersedes: "D-009" }),
    decision("D-011", { supersedes: "D-010" }),
  ] }));
  for (const id of ["D-009", "D-010", "D-011"]) {
    assert.deepEqual(m.decisionChain(id).map(d => d.id), ["D-009", "D-010", "D-011"], id);
  }
  assert.deepEqual(m.decisionChain("D-001").map(d => d.id), ["D-001"]);
  assert.deepEqual(m.decisionChain("D-404"), []);
});

test("decision chains stop at cycles and dangling references", () => {
  const m = createVaultModel(index([], { decisions: [
    decision("D-001", { supersedes: "D-002" }),
    decision("D-002", { supersedes: "D-001" }),
    decision("D-003", { supersedes: "D-999", supersededBy: "D-998" }),
  ] }));
  assert.deepEqual(m.decisionChain("D-001").map(d => d.id), ["D-002", "D-001"]);
  assert.deepEqual(m.decisionChain("D-003").map(d => d.id), ["D-003"]);
});
