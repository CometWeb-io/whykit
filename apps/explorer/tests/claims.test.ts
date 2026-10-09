import assert from "node:assert/strict";
import test from "node:test";
import { layoutGraph } from "../src/lib/graph.ts";
import { createVaultModel } from "../src/lib/model.ts";
import { index, doc } from "./fixtures.ts";
const claim = () => doc("00-context/claims/c-001-offline-reads", { type: "claim", claimId: "C-001", statement: "Cached decisions are readable offline.", scope: "Desktop v2.1", validFrom: "2026-10-01", lastVerified: "2026-10-09", verificationStatus: "disputed", verificationReasons: ["conflicting_evidence"], claimRelations: [
  { evidence_id: "E-001", relation: "supports", snapshot: `00-context/claim-snapshots/${"a".repeat(64)}.txt`, source_snapshot_hash: "a".repeat(64), fragment: "lines:1-1", observed_at: "2026-10-09", rationale: "Observed", usable: true, reasons: [] },
  { evidence_id: "E-002", relation: "contradicts", snapshot: `00-context/claim-snapshots/${"b".repeat(64)}.txt`, source_snapshot_hash: "b".repeat(64), fragment: "lines:1-1", observed_at: "2026-10-09", rationale: "Conflicting observation", usable: true, reasons: [] },
] });
test("retains both conflict sides and typed decision links", () => {
  const c = claim(), d = doc("06-decisions/d-001-review", { claimIds: ["C-001"] });
  const model = createVaultModel(index([c, d], { contract_version: 2 }));
  assert.equal(model.resolveDoc("C-001")?.verificationStatus, "disputed");
  assert.deepEqual(model.resolveDoc("C-001")?.claimRelations?.map(r => r.relation), ["supports", "contradicts"]);
  assert.deepEqual(model.linksFor(d), [c]);
  assert.equal(layoutGraph([c, d], model.linksFor).edges[0]?.type, "claim");
});
test("loads existing v1 fixtures", () => { assert.equal(createVaultModel(index([doc("Home")], { contract_version: 1 })).vault.docs.length, 1); });
test("rejects unsupported versions and malformed claim payloads", () => {
  assert.throws(() => createVaultModel(index([], { contract_version: 99 })), /version/);
  assert.throws(() => createVaultModel(index([claim()], { contract_version: 1 })), /claim/i);
  const c = claim(); c.claimRelations![0]!.relation = "invented" as "supports";
  assert.throws(() => createVaultModel(index([c], { contract_version: 2 })), /claim/i);
});
