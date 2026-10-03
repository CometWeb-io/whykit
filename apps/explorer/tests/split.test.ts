import { test } from "node:test";
import assert from "node:assert/strict";
import { createVaultModel } from "../src/lib/model.ts";
import { reviewCues, splitIndex } from "../src/lib/split.ts";
import { doc, index } from "./fixtures.ts";

const full = () => index([
  doc("a/one", { title: "One", body: "Links to [[two]] and [[two|again]].\n\n## Open questions\n\nNeeds verification" }),
  doc("a/two", { title: "Two", body: "Back to [[a/one]], plus `[[not-a-link]]` in code." }),
  doc("b/three", { title: "Three", body: "The renewal pipeline lives only in this body." }),
]);

test("the summary carries no note bodies; the bodies chunk carries every one", () => {
  const { summary, bodies } = splitIndex(full());
  for (const d of summary.docs) assert.equal("body" in d, false, `${d.id} kept its body`);
  assert.deepEqual(Object.keys(bodies).sort(), ["a/one", "a/two", "b/three"]);
  assert.match(bodies["b/three"]!, /renewal pipeline/);
});

test("the summary resolves links exactly as the full index does", () => {
  const source = full();
  const before = createVaultModel(source);
  const { summary } = splitIndex(source);
  const after = createVaultModel(summary);
  for (const d of source.docs) {
    const id = d.id;
    assert.deepEqual(after.linksFor(after.resolveDoc(id)!).map(x => x.id), before.linksFor(d).map(x => x.id), `links of ${id}`);
    assert.deepEqual(after.backlinksFor(after.resolveDoc(id)!).map(x => x.id), before.backlinksFor(d).map(x => x.id), `backlinks of ${id}`);
  }
  assert.deepEqual(summary.docs.find(d => d.id === "a/two")?.links, [0]);
});

test("review cues are counted at split time", () => {
  assert.equal(reviewCues("## Open questions\nNeeds verification, needs verification"), 3);
  const { summary } = splitIndex(full());
  assert.equal(summary.docs.find(d => d.id === "a/one")?.cues, 2);
  assert.equal(createVaultModel(summary).openCues(), 2);
  assert.equal(createVaultModel(full()).openCues(), 2);
});

test("search covers metadata at once and note text after the bodies arrive", () => {
  const { summary, bodies } = splitIndex(full());
  const m = createVaultModel(summary);
  assert.equal(m.hasBodies(), false);
  assert.deepEqual(m.searchDocs("three").map(d => d.id), ["b/three"]);
  assert.deepEqual(m.searchDocs("renewal pipeline"), []);
  assert.equal(m.bodyOf(m.resolveDoc("b/three")!), undefined);
  m.attachBodies(bodies);
  assert.equal(m.hasBodies(), true);
  assert.deepEqual(m.searchDocs("renewal pipeline").map(d => d.id), ["b/three"]);
  assert.match(m.bodyOf(m.resolveDoc("b/three")!) ?? "", /renewal pipeline/);
});

test("a full index needs no second chunk", () => {
  const m = createVaultModel(full());
  assert.equal(m.hasBodies(), true);
  assert.deepEqual(m.searchDocs("renewal pipeline").map(d => d.id), ["b/three"]);
});

test("a body chunk from another build is rejected", () => {
  const { summary } = splitIndex(full());
  const m = createVaultModel(summary);
  assert.throws(() => m.attachBodies({ "a/one": "x" }), /does not match/);
  assert.equal(m.hasBodies(), false);
});

test("lint findings go to their own chunk; the summary keeps the counts", () => {
  const findings = [
    { path: "a/one.md", line: 3, level: "warning" as const, code: "fact.evidence_missing", message: "fact callout cites nothing" },
    { path: "a/two.md", level: "warning" as const, code: "markdown_link.missing", message: "local Markdown link does not exist" },
  ];
  const source = index(full().docs, { lint: { files: 3, errors: 0, warnings: 2, findings } });
  const { summary, findings: chunk } = splitIndex(source);
  assert.equal("findings" in summary.lint, false, "the summary still carries the findings");
  assert.deepEqual({ ...summary.lint }, { files: 3, errors: 0, warnings: 2 });
  assert.deepEqual(chunk, findings);
});

test("findings are available at once from a full index and after attaching from a split one", () => {
  const findings = [{ path: "a/one.md", level: "error" as const, code: "x.y", message: "broken" }];
  const source = index(full().docs, { lint: { files: 3, errors: 1, warnings: 0, findings } });
  assert.deepEqual(createVaultModel(source).findings(), findings);
  const { summary, findings: chunk } = splitIndex(source);
  const m = createVaultModel(summary);
  assert.equal(m.findings(), undefined);
  m.attachFindings(chunk);
  assert.deepEqual(m.findings(), findings);
});

test("a findings chunk that does not match the counts is rejected", () => {
  const source = index(full().docs, { lint: { files: 3, errors: 1, warnings: 0, findings: [{ path: "a/one.md", level: "error", code: "x.y", message: "broken" }] } });
  const m = createVaultModel(splitIndex(source).summary);
  assert.throws(() => m.attachFindings([]), /does not match/);
  assert.equal(m.findings(), undefined);
});
