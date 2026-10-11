import assert from "node:assert/strict";
import test from "node:test";
import { fenceStart, maskMarkdownCode } from "../src/lib/code.ts";
import { extractHeadings, extractWikilinks, parseInline, parseMarkdown } from "../src/lib/markdown.ts";

const BT = String.fromCharCode(96);
const link = "[[E-001]]";
const allowed = "[[E-002]]";

test("variable code spans never create evidence or wikilinks", () => {
  const content = "Before " + BT.repeat(2) + link + BT.repeat(2) + " " + allowed;
  assert.deepEqual(extractWikilinks(content), ["E-002"]);
  assert.deepEqual(parseInline(BT.repeat(2) + link + BT.repeat(2)).map(node => node.t), ["code"]);
});

test("unterminated fence extends to end of file", () => {
  assert.deepEqual(extractWikilinks(BT.repeat(3) + "md\n" + link), []);
});

test("shorter and mixed closers do not end a fence", () => {
  for (const [open, premature, close] of [
    [BT.repeat(4), BT.repeat(3), BT.repeat(4)],
    ["~~~~", "~~~", "~~~~"],
    ["~~~", BT.repeat(3), "~~~"],
  ]) {
    const body = open + "markdown\n" + premature + "\n" + link + "\n" + close + "\n" + allowed;
    assert.deepEqual(extractWikilinks(body), ["E-002"]);
    const code = parseMarkdown(body)[0];
    assert.equal(code?.t, "code");
    if (code?.t === "code") assert.ok(code.code.includes(link));
  }
});

test("tilde fenced blocks and three-space indented fenced blocks are rendered as code", () => {
  const src = "   ~~~md\n" + link + "\n   ~~~\n";
  const block = parseMarkdown(src)[0];
  assert.equal(block?.t, "code");
  if (block?.t === "code") assert.equal(block.lang, "md");
  assert.deepEqual(extractWikilinks(src), []);
  assert.equal(fenceStart("    ~~~md"), null);
});

test("mask preserves offsets and all original newlines", () => {
  const src = "x\n" + BT.repeat(3) + "x\n" + link + "\n" + BT.repeat(3) + "\n" + allowed;
  const masked = maskMarkdownCode(src);
  assert.equal(masked.length, src.length);
  for (let i = 0; i < src.length; i++) if (src[i] === "\n") assert.equal(masked[i], "\n");
  assert.equal(masked.includes(link), false);
  assert.equal(masked.includes(allowed), true);
});

test("CR-only fenced examples cannot forge Explorer graph links", () => {
  for (const fence of [BT.repeat(3), "~~~"]) {
    const src = fence + "md\r" + link + "\r" + fence + "\r" + allowed;
    assert.deepEqual(extractWikilinks(src), ["E-002"]);
    const first = parseMarkdown(src)[0];
    assert.equal(first?.t, "code");
    if (first?.t === "code") assert.ok(first.code.includes(link));
  }
});

test("mixed line endings and CR-only code spans preserve graph semantics", () => {
  const fence = "~~~\r" + link + "\r\n~~~\n" + allowed;
  assert.deepEqual(extractWikilinks(fence), ["E-002"]);
  const src = BT.repeat(2) + "a\rb" + BT.repeat(2) + " " + allowed;
  const parsed = parseInline(BT.repeat(2) + "a\rb" + BT.repeat(2));
  assert.equal(parsed[0]?.t, "code");
  if (parsed[0]?.t === "code") assert.equal(parsed[0].v, "a b");
  assert.deepEqual(extractWikilinks(src), ["E-002"]);
});

test("CR-only heading extraction excludes headings hidden inside fences", () => {
  const body = "## Real\r~~~\r## Example only\r~~~\r## After\r\n";
  assert.deepEqual(extractHeadings(body).map(h => h.text), ["Real", "After"]);
  const inline = "## Heading " + BT + "code" + BT + "\r";
  assert.deepEqual(extractHeadings(inline).map(h => h.text), ["Heading " + BT + "code" + BT]);
});

test("invalid backtick info string is not considered a fence", () => {
  const src = BT.repeat(3) + "bad" + BT + "\n" + allowed;
  assert.deepEqual(extractWikilinks(src), ["E-002"]);
});

test("shared comment corpus agrees on visible wikilinks and preserves UTF-16 offsets", async () => {
  const { readFileSync } = await import("node:fs");
  const cases = JSON.parse(
    readFileSync(new URL("../../../tests/fixtures/markdown-code-comments.json", import.meta.url), "utf8")
  ) as { id: string; markdown: string; evidence: string[]; wikilinks: string[] }[];
  for (const example of cases) {
    assert.deepEqual(extractWikilinks(example.markdown), example.wikilinks, example.id);
    const masked = maskMarkdownCode(example.markdown);
    assert.equal(masked.length, example.markdown.length, example.id);
    for (let i = 0; i < example.markdown.length; i++) {
      if ("\r\n".includes(example.markdown[i]!)) {
        assert.equal(masked[i], example.markdown[i], example.id);
      }
    }
  }
});

test("HTML-commented headings never become Explorer navigation entries", () => {
  const markdown = "<" + "!--\n## Hidden section\n-->" + "\n## Actual section";
  assert.deepEqual(extractHeadings(markdown).map((heading) => heading.text), ["Actual section"]);
});

test("comment lexical precedence scales with numerous inline delimiters", () => {
  const source = ("<!-- " + BT + "body --> " + BT + " ").repeat(2500) + allowed;
  const masked = maskMarkdownCode(source);
  assert.equal(masked.length, source.length);
  assert.ok(masked.includes(allowed));
});

test("a very large number of unmatched delimiters terminates", () => {
  const src = (BT + " text " + BT.repeat(2) + " x ").repeat(10000) + allowed;
  assert.deepEqual(extractWikilinks(src), ["E-002"]);
});
