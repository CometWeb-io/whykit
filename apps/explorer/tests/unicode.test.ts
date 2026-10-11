import assert from "node:assert/strict";
import test from "node:test";
import { foldText } from "../src/lib/unicode.ts";
import { createVaultModel } from "../src/lib/model.ts";
import { doc, index } from "./fixtures.ts";
test("Unicode NFC and full casefold semantics", () => {
  assert.equal(foldText("Straße"), "strasse");
  assert.equal(foldText("Cafe\u0301"), "café");
  assert.equal(foldText("ΟΣ"), "οσ");
  assert.equal(foldText("ς"), "σ");
  assert.equal(foldText("ﬃ"), "ffi");
  assert.equal(foldText("\uAB70"), "\u13A0");
  assert.equal(foldText("\u13A0"), "\u13A0");
});
test("Explorer fulltext and aliases honor Unicode casefold", () => {
  const m = createVaultModel(index([doc("a/Straße", {title:"Straße", aliases:["Fußgänger"], body:"Cafe\u0301 ΟΣ"})]));
  assert.equal(m.resolveDoc("STRASSE")?.id, "a/Straße");
  assert.equal(m.resolveDoc("FUSSGÄNGER")?.id, "a/Straße");
  assert.deepEqual(m.searchDocs("strasse").map(d=>d.id), ["a/Straße"]);
  assert.deepEqual(m.searchDocs("CAFÉ").map(d=>d.id), ["a/Straße"]);
  assert.deepEqual(m.searchDocs("ος").map(d=>d.id), ["a/Straße"]);
});
