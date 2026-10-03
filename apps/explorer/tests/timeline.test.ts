import { test } from "node:test";
import assert from "node:assert/strict";
import { axisLabels, decisionTimeline, filterTimeline, parseDay, sortTimeline, timelineFacets, timelineScale } from "../src/lib/timeline.ts";
import type { DecisionRow } from "../src/types.ts";
import { doc } from "./fixtures.ts";

const row = (id: string, date: string, over: Partial<DecisionRow> = {}): DecisionRow =>
  ({ id, title: id, date, owner: "Ops", status: "accepted", recordId: `d/${id}`, ...over });

const docs = new Map([
  ["d/D-001", doc("d/D-001", { tags: ["pricing"] })],
  ["d/D-002", doc("d/D-002", { tags: ["pricing", "crm"] })],
  ["d/D-003", doc("d/D-003", { tags: ["crm"] })],
]);
const decisions = [
  row("D-001", "2026-01-10", { status: "superseded", supersededBy: "D-002" }),
  row("D-002", "2026-03-05", { supersedes: "D-001", owner: "Sales" }),
  row("D-003", "2026-02-30", { status: "proposed" }),
];
const entries = decisionTimeline(decisions, id => docs.get(id));

test("parseDay rejects rolled-over and malformed dates", () => {
  assert.equal(parseDay("2026-02-30"), null);
  assert.equal(parseDay("2026-3-5"), null);
  assert.equal(parseDay("2026-03-05"), Date.UTC(2026, 2, 5));
});

test("a superseded decision ends on its successor's date; a live one stays open", () => {
  const [d1, d2, d3] = entries;
  assert.equal(d1!.end, Date.UTC(2026, 2, 5));
  assert.equal(d1!.successor?.id, "D-002");
  assert.equal(d1!.superseded, true);
  assert.equal(d2!.end, null);
  assert.equal(d2!.superseded, false);
  assert.equal(d3!.start, null, "invalid dates do not invent a position");
});

test("a successor is found from `supersedes` when supersededBy is missing", () => {
  const [old] = decisionTimeline([row("D-1", "2026-01-01"), row("D-2", "2026-02-01", { supersedes: "D-1" })], () => undefined);
  assert.equal(old!.successor?.id, "D-2");
  assert.equal(old!.end, Date.UTC(2026, 1, 1));
});

test("filters combine status, owner and tag", () => {
  assert.deepEqual(filterTimeline(entries, { tag: "pricing" }).map(e => e.row.id), ["D-001", "D-002"]);
  assert.deepEqual(filterTimeline(entries, { tag: "pricing", owner: "Sales" }).map(e => e.row.id), ["D-002"]);
  assert.deepEqual(filterTimeline(entries, { status: "proposed" }).map(e => e.row.id), ["D-003"]);
  assert.equal(filterTimeline(entries, {}).length, 3);
});

test("facets count values and sort them", () => {
  const f = timelineFacets(entries);
  assert.deepEqual(f.tags, [{ value: "crm", count: 2 }, { value: "pricing", count: 2 }]);
  assert.deepEqual(f.owners.map(o => o.value), ["Ops", "Sales"]);
});

test("the scale spans first decision to today with month ticks", () => {
  const today = Date.UTC(2026, 5, 15);
  const scale = timelineScale(entries, today)!;
  assert.equal(scale.min, Date.UTC(2026, 0, 1));
  assert.equal(scale.max, Date.UTC(2026, 6, 1));
  assert.deepEqual(scale.ticks.map(t => t.label), ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"]);
  assert.equal(scale.pos(scale.min), 0);
  assert.equal(scale.pos(scale.max), 100);
});

test("long spans switch to quarter and year ticks", () => {
  const span = (from: string, to: string) => timelineScale(decisionTimeline([row("A", from), row("B", to)], () => undefined), 0)!;
  assert.deepEqual(span("2025-01-15", "2026-06-01").ticks.slice(0, 2).map(t => t.label), ["Q1 2025", "Q2 2025"]);
  assert.deepEqual(span("2019-05-01", "2026-03-01").ticks.slice(0, 2).map(t => t.label), ["2020", "2021"]);
  assert.equal(timelineScale([], 0), null);
});

test("sorting puts undated entries last when newest first", () => {
  assert.deepEqual(sortTimeline(entries, "newest").map(e => e.row.id), ["D-002", "D-001", "D-003"]);
  assert.deepEqual(sortTimeline(entries, "oldest").map(e => e.row.id), ["D-003", "D-001", "D-002"]);
});

test("axis labels are thinned by the measured track width", () => {
  const ticks = Array.from({ length: 9 }, (_, i) => ({ at: i * 11, label: `2026-0${i + 1}` }));
  const wide = axisLabels(ticks, 1000).filter(t => t.label).length;
  const narrow = axisLabels(ticks, 300).filter(t => t.label).length;
  assert.equal(wide, 9);
  assert.ok(narrow < wide && narrow >= 3, `${narrow}`);
  assert.equal(axisLabels(ticks, 0).filter(t => t.label).length, 0);
});
