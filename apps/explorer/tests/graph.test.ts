import { test } from "node:test";
import assert from "node:assert/strict";
import { layoutGraph, moveFocus, neighbourhood, NODE_H, NODE_W, typeahead } from "../src/lib/graph.ts";
import type { VaultDoc } from "../src/types.ts";
import { doc } from "./fixtures.ts";

const noLinks = () => [] as VaultDoc[];

function overlaps(a: { x: number; y: number }, b: { x: number; y: number }) {
  return a.x < b.x + NODE_W && b.x < a.x + NODE_W && a.y < b.y + NODE_H && b.y < a.y + NODE_H;
}

test("nodes never overlap, even when one workstream is crowded", () => {
  const docs = [
    ...Array.from({ length: 37 }, (_, i) => doc(`big/n${i}`)),
    ...Array.from({ length: 4 }, (_, i) => doc(`small/n${i}`)),
    ...Array.from({ length: 12 }, (_, i) => doc(`mid/n${i}`)),
  ];
  const { nodes, width, height } = layoutGraph(docs, noLinks, { maxRows: 10, maxCols: 5 });
  assert.equal(nodes.length, docs.length);
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i]!;
    assert.ok(a.x >= 0 && a.y >= 0 && a.x + NODE_W <= width && a.y + NODE_H <= height, `${a.d.id} outside canvas`);
    for (let j = i + 1; j < nodes.length; j++) {
      assert.ok(!overlaps(a, nodes[j]!), `${a.d.id} overlaps ${nodes[j]!.d.id}`);
    }
  }
});

test("only edges between drawn notes are kept", () => {
  const a = doc("w/a"), b = doc("w/b"), hidden = doc("w/hidden");
  const links = new Map([[a.id, [b, hidden]], [b.id, [a]]]);
  const { edges } = layoutGraph([a, b], d => links.get(d.id) || []);
  assert.deepEqual(edges, [{ from: "w/a", to: "w/b" }, { from: "w/b", to: "w/a" }]);
});

test("empty vault still yields a drawable canvas", () => {
  const layout = layoutGraph([], noLinks);
  assert.equal(layout.nodes.length, 0);
  assert.ok(layout.width > 0 && layout.height > 0);
});

test("neighbourhood is the note plus direct links in either direction", () => {
  const edges = [{ from: "a", to: "b" }, { from: "c", to: "a" }, { from: "b", to: "d" }];
  assert.deepEqual([...neighbourhood(edges, "a")].sort(), ["a", "b", "c"]);
  assert.equal(neighbourhood(edges, null).size, 0);
});

test("arrow keys walk the list order and across columns by position", () => {
  const docs = [...Array.from({ length: 12 }, (_, i) => doc(`a/n${i}`)), ...Array.from({ length: 3 }, (_, i) => doc(`b/m${i}`))];
  const { nodes } = layoutGraph(docs, noLinks, { maxRows: 5, maxCols: 5 });
  const at = (id: string) => nodes.findIndex(n => n.d.id === id);
  const go = (from: string, key: string) => nodes[moveFocus(nodes, at(from), key)]?.d.id;
  assert.equal(go("a/n0", "ArrowDown"), "a/n1");
  assert.equal(go("a/n4", "ArrowDown"), "a/n5");
  assert.equal(go("a/n0", "ArrowUp"), "a/n0");
  assert.equal(go("a/n1", "ArrowRight"), "a/n6");
  assert.equal(go("a/n6", "ArrowLeft"), "a/n1");
  assert.equal(go("a/n0", "ArrowLeft"), "a/n0");
  assert.equal(go("a/n11", "ArrowRight"), "b/m1");
  assert.equal(go("a/n7", "Home"), "a/n0");
  assert.equal(go("a/n7", "End"), "b/m2");
  assert.equal(go("b/m2", "ArrowDown"), "b/m2");
  assert.equal(moveFocus(nodes, 0, "x"), -1);
  assert.equal(moveFocus([], 0, "ArrowDown"), -1);
});

test("type-ahead finds the next title with the typed prefix and wraps", () => {
  const docs = [doc("w/alpha", { title: "Alpha" }), doc("w/beta", { title: "Beta" }), doc("w/apple", { title: "apple pie" }), doc("w/bravo", { title: "Bravo" })];
  const { nodes } = layoutGraph(docs, noLinks);
  const id = (i: number) => nodes[i]?.d.id;
  assert.equal(id(typeahead(nodes, 0, "a")), "w/apple");
  assert.equal(id(typeahead(nodes, 2, "a")), "w/alpha");
  assert.equal(id(typeahead(nodes, 0, "br")), "w/bravo");
  assert.equal(id(typeahead(nodes, 3, "bravo")), "w/bravo");
  assert.equal(typeahead(nodes, 0, "zz"), -1);
});
