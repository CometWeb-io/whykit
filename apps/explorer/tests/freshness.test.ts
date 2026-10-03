import { test } from "node:test";
import assert from "node:assert/strict";
import { classify, describeAge, evidenceFreshness } from "../src/lib/freshness.ts";
import type { EvidenceRow, VaultDoc, VaultPolicy } from "../src/types.ts";
import { doc } from "./fixtures.ts";

const today = Date.UTC(2026, 8, 17);
const policy: VaultPolicy = { evidenceAccessAgeDays: { analytics: 100 }, decisionReviewDays: 90, statusDueDays: 30 };
const ev = (id: string, over: Partial<EvidenceRow> = {}): EvidenceRow =>
  ({ id, state: "active", source: id, type: "analytics", date: "", accessed: "", location: "", claims: "", ...over });

test("access age is judged like lint: only Accessed, strictly over the window", () => {
  assert.equal(classify(ev("a", { accessed: "2026-06-09" }), policy, today).state, "due"); // exactly 100 days: not over
  assert.equal(classify(ev("a", { accessed: "2026-08-01" }), policy, today).state, "fresh");
  assert.equal(classify(ev("a", { accessed: "2026-06-08" }), policy, today).state, "stale"); // 101 days
  assert.equal(classify(ev("a", { accessed: "2026-06-20" }), policy, today).state, "due"); // 89 > 80% of 100
  assert.equal(classify(ev("a", { accessed: "2026-09-18" }), policy, today).state, "future");
  const missing = classify(ev("a", { date: "2026-01-01" }), policy, today);
  assert.equal(missing.state, "missing");
  assert.equal(missing.basis, "date");
});

test("types without a window are unchecked but still show their age", () => {
  const out = classify(ev("a", { type: "interview", accessed: "2025-09-17" }), policy, today);
  assert.deepEqual(out, { state: "unchecked", ageDays: 365, basis: "accessed", maxAge: null });
  assert.equal(classify(ev("a"), undefined, today).state, "unchecked");
});

test("retired evidence separates live citers from history", () => {
  const live = doc("w/live"), old = doc("w/old", { status: "superseded" }), arch = doc("w/arch", { status: "archived" });
  const citers = new Map<string, VaultDoc[]>([["E-001", [live, old, arch]], ["E-002", [old]]]);
  const { retired } = evidenceFreshness(
    [ev("E-002", { state: "retired" }), ev("E-001", { state: "retired", replacedBy: "E-003" }), ev("E-003", { accessed: "2026-09-01" })],
    policy, today, id => citers.get(id) ?? [],
  );
  assert.deepEqual(retired.map(r => r.e.id), ["E-001", "E-002"], "rows still cited by live notes come first");
  assert.deepEqual(retired[0]!.liveCiters.map(d => d.id), ["w/live"]);
  assert.deepEqual(retired[0]!.historicalCiters.map(d => d.id), ["w/old", "w/arch"]);
  assert.equal(retired[0]!.replacement?.id, "E-003");
});

test("active rows sort by severity, then by how many notes cite them", () => {
  const citers = new Map<string, VaultDoc[]>([["E-2", [doc("a"), doc("b")]]]);
  const { active } = evidenceFreshness(
    [ev("E-1", { accessed: "2026-09-10" }), ev("E-2", { accessed: "2026-01-01" }), ev("E-3", { accessed: "2026-01-01" }), ev("E-4", { type: "x" })],
    policy, today, id => citers.get(id) ?? [],
  );
  assert.deepEqual(active.map(r => `${r.e.id}:${r.state}`), ["E-2:stale", "E-3:stale", "E-1:fresh", "E-4:unchecked"]);
});

test("ages read naturally", () => {
  assert.equal(describeAge(null), "no date");
  assert.equal(describeAge(0), "today");
  assert.equal(describeAge(1), "1 day");
  assert.equal(describeAge(-2), "2 days ahead");
});
