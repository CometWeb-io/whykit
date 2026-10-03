import { test } from "node:test";
import assert from "node:assert/strict";
import { canvasMeasure, fitLabel } from "../src/lib/label.ts";

// One unit per character, except narrow letters, so width is not a character count.
const measure = (s: string) => [...s].reduce((w, c) => w + ("il.,' ".includes(c) ? 0.5 : 1), 0);

test("short labels are left alone", () => {
  assert.deepEqual(fitLabel("Pricing", 20, measure), { lines: ["Pricing"], truncated: false });
});

test("labels wrap at spaces before they are cut", () => {
  assert.deepEqual(fitLabel("Evidence register for pricing", 16, measure), { lines: ["Evidence register", "for pricing"], truncated: false });
});

test("only real overflow is ellipsized, and every line fits", () => {
  const out = fitLabel("D-011 — Replace the direct-only motion with a partner channel pilot", 20, measure);
  assert.equal(out.lines.length, 2);
  assert.equal(out.truncated, true);
  assert.ok(out.lines[1]!.endsWith("…"));
  for (const line of out.lines) assert.ok(measure(line) <= 20, line);
});

test("narrow characters fit more than a fixed character budget would", () => {
  // 30 characters, but many narrow ones: fits a 26-unit line uncut.
  const text = "illicit little lists filling i";
  assert.equal(text.length, 30);
  assert.deepEqual(fitLabel(text, 26, measure, 1), { lines: [text], truncated: false });
});

test("a single word wider than the line is split, then ellipsized", () => {
  const out = fitLabel("Supercalifragilisticexpialidocious", 10, measure);
  assert.equal(out.lines[0], "Supercalifr"); // "l" and "i" are narrow
  assert.ok(out.lines[1]!.endsWith("…"));
  assert.ok(measure(out.lines[1]!) <= 10);
});

test("one-line mode ellipsizes the first line", () => {
  assert.deepEqual(fitLabel("alpha beta gamma", 10, measure, 1), { lines: ["alpha beta…"], truncated: true });
});

test("empty titles produce one empty line", () => {
  assert.deepEqual(fitLabel("   ", 10, measure), { lines: [""], truncated: false });
});

test("canvasMeasure falls back to a character width without a DOM", () => {
  const m = canvasMeasure("11px sans-serif", 6);
  assert.equal(m("abcd"), 24);
  assert.equal(m("abcd"), 24);
});
