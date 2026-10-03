import { test } from "node:test";
import assert from "node:assert/strict";
import { hrefFor, parseHash, routeHref, withParams, type Route } from "../src/lib/route.ts";

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

test("view state round-trips through the hash", () => {
  const route = parseHash(hrefFor("timeline", undefined, { owner: "Revenue Ops", status: "accepted", tag: "pricing & tiers" }));
  assert.deepEqual(route, { view: "timeline", params: { owner: "Revenue Ops", status: "accepted", tag: "pricing & tiers" } });
  assert.deepEqual(parseHash(hrefFor("doc", "a/b", { search: "why not" })), { view: "doc", doc: "a/b", params: { search: "why not" } });
});

test("hrefs are canonical: sorted keys and %20 for spaces", () => {
  assert.equal(hrefFor("graph", undefined, { ws: "01-strategy", node: "a b" }), "#view=graph&node=a%20b&ws=01-strategy");
  assert.equal(hrefFor("doc", "06-decisions/d-010-direct"), "#doc=06-decisions%2Fd-010-direct");
});

test("an empty search value still counts as state", () => {
  // `search=` means the search dialog is open with no query yet.
  assert.deepEqual(parseHash("#view=home&search="), { view: "home", params: { search: "" } });
});

test("withParams patches and removes params", () => {
  const base: Route = { view: "evidence", params: { q: "crm", state: "stale" } };
  assert.deepEqual(withParams(base, { q: null }), { view: "evidence", params: { state: "stale" } });
  assert.deepEqual(withParams(base, { q: null, state: undefined }), { view: "evidence" });
  assert.deepEqual(withParams({ view: "home" }, { search: "" }), { view: "home", params: { search: "" } });
  assert.deepEqual(withParams({ view: "doc", doc: "x" }, { search: "hi" }), { view: "doc", doc: "x", params: { search: "hi" } });
  assert.equal(routeHref(withParams(base, { q: "new" })), "#view=evidence&q=new&state=stale");
});
