import { test } from "node:test";
import assert from "node:assert/strict";
import { hrefFor, parseHash } from "../src/lib/route.ts";

test("empty hash is home", () => {
  assert.deepEqual(parseHash(""), { view: "home" });
  assert.deepEqual(parseHash("#"), { view: "home" });
});

test("known views and documents round-trip", () => {
  assert.deepEqual(parseHash(hrefFor("graph")), { view: "graph" });
  const id = "06-decisions/D-001 — Ünïcode & spaces";
  assert.deepEqual(parseHash(hrefFor("doc", id)), { view: "doc", doc: id });
});

test("unknown view falls back to home", () => {
  assert.deepEqual(parseHash("#view=nope"), { view: "home" });
  assert.deepEqual(parseHash("#view=doc"), { view: "home" });
});

test("plain in-page anchors are not routes", () => {
  // The skip link and heading anchors must not navigate away from the page.
  assert.equal(parseHash("#content"), null);
  assert.equal(parseHash("#open-questions"), null);
});
