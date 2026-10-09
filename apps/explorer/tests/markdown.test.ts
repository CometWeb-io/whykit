import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createSlugger, extractWikilinks, parseInline, parseMarkdown, slugify } from "../src/lib/markdown.ts";

test("repeated headings get unique ids", () => {
  const slug = createSlugger();
  assert.deepEqual(["Notes", "Notes", "Notes-1", "Notes"].map(slug), ["notes", "notes-1", "notes-1-1", "notes-2"]);
  assert.equal(createSlugger()("!!!"), "section");
});

test("slugify folds accents and wikilink labels", () => {
  assert.equal(slugify("Zażółć [[target|gęślą]] jaźń"), "zazołc-gesla-jazn");
});

test("wikilinks in code are not graph edges", () => {
  const body = "See [[Alpha]] and [[Beta|b]].\n`[[Gamma]]`\n```\n[[Delta]]\n```\n[[Alpha#Heading]]";
  assert.deepEqual(extractWikilinks(body), ["Alpha", "Beta", "Alpha"]);
});

test("unsafe link schemes stay links the renderer can refuse", () => {
  const [node] = parseInline("[x](javascript:alert(1))");
  assert.equal(node?.t, "link");
});

test("malformed block syntax still terminates", () => {
  const blocks = parseMarkdown("##### deep\n| not a table\n>x\n```\nunterminated");
  assert.ok(blocks.length >= 3);
});

test("tables keep wikilink pipes inside one cell", () => {
  const [table] = parseMarkdown("| A | B |\n|---|---|\n| [[x|label]] | 2 |");
  assert.equal(table?.t, "table");
  if (table?.t === "table") assert.equal(table.rows[0]?.length, 2);
});

test("table rows follow the same escaped-pipe contract as Python", () => {
  const fixtures = JSON.parse(readFileSync(new URL("../../../tests/fixtures/table-rows.json", import.meta.url), "utf8")) as {row: string; cells: string[]}[];
  for (const {row, cells} of fixtures) {
    const [table] = parseMarkdown(`| A | B |\n|---|---|\n${row}`);
    assert.equal(table?.t, "table", row);
    if (table?.t === "table") assert.deepEqual(table.rows[0], cells.map(parseInline), row);
  }
});

test("wrapped quote and callout lines join into paragraphs", () => {
  const [quote, callout] = parseMarkdown("> one\n> two\n>\n> three\n\n> [!fact] Title\n> a\n> b");
  assert.equal(quote?.t, "quote");
  if (quote?.t === "quote") assert.equal(quote.children.length, 2);
  assert.equal(callout?.t, "callout");
  if (callout?.t === "callout") {
    assert.equal(callout.title, "Title");
    assert.equal(callout.children.length, 1);
  }
});
