import { test } from "node:test";
import assert from "node:assert/strict";
import { describeDue, reviewQueue } from "../src/lib/reviews.ts";
import { doc } from "./fixtures.ts";

const NOW = new Date("2026-09-17T23:30:00Z");

test("queue keeps approved docs due within the window, most urgent first", () => {
  const docs = [
    doc("a", { title: "Later", reviewBy: "2026-10-10" }),
    doc("b", { title: "Overdue", reviewBy: "2026-09-10" }),
    doc("c", { title: "Today", reviewBy: "2026-09-17" }),
    doc("d", { title: "Far", reviewBy: "2027-01-01" }),
    doc("e", { title: "Draft", status: "draft", reviewBy: "2026-09-01" }),
    doc("f", { title: "Undated", reviewBy: "TODO" }),
  ];
  const queue = reviewQueue(docs, NOW, 30);
  assert.deepEqual(queue.map(x => [x.d.title, x.days]), [["Overdue", -7], ["Today", 0], ["Later", 23]]);
});

test("impossible calendar dates are ignored rather than producing NaN", () => {
  const queue = reviewQueue([doc("x", { reviewBy: "2026-13-45" })], NOW, 1000);
  assert.ok(queue.every(x => Number.isFinite(x.days)));
});

test("due labels are pluralised", () => {
  assert.equal(describeDue(-1), "1 day overdue");
  assert.equal(describeDue(-3), "3 days overdue");
  assert.equal(describeDue(0), "due today");
  assert.equal(describeDue(1), "due in 1 day");
  assert.equal(describeDue(9), "due in 9 days");
});
