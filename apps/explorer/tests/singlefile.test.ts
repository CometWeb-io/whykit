import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { decodeChunk } from "../src/lib/chunks.ts";
import { buildSingleFile } from "../src/lib/singlefile.ts";

const HTML = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>WhyKit Explorer</title>
    <script type="module" crossorigin src="./assets/index-abc.js"></script>
    <link rel="stylesheet" crossorigin href="./assets/index-def.css">
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>
`;
const SCRIPT = 'const s = "</script><script>alert(1)</script>"; console.log(s);';
const STYLE = "body{color:red}";
const parts = () => ({ html: HTML, scriptFile: "assets/index-abc.js", script: SCRIPT, styleFile: "assets/index-def.css", style: STYLE, chunks: { bodies: '{"a":"b"}', findings: "[]" } });
const sha = (text: string) => `'sha256-${createHash("sha256").update(text, "utf8").digest("base64")}'`;
const inlineScript = (html: string) => html.match(/<script type="module">([\s\S]*?)<\/script>/)?.[1];

test("the single file references no other file", () => {
  const { html } = buildSingleFile(parts());
  assert.doesNotMatch(html, /\s(src|href)\s*=/i);
  assert.match(html, /<style>body\{color:red\}<\/style>/);
});

test("the inlined script cannot close its own element early", () => {
  const { html } = buildSingleFile(parts());
  const body = inlineScript(html);
  assert.ok(body, "no inline module script");
  assert.doesNotMatch(body, /<\/script/i);
  assert.match(body, /<\\\/script>/);
});

test("the policy allows exactly the inlined script and style, by hash", () => {
  const { html, csp } = buildSingleFile(parts());
  const body = inlineScript(html)!;
  assert.ok(csp.includes(`script-src ${sha(body)}`), csp);
  assert.ok(csp.includes(`style-src ${sha(STYLE)}`), csp);
  for (const d of ["default-src 'none'", "connect-src 'none'", "object-src 'none'", "base-uri 'none'", "form-action 'none'"]) assert.ok(csp.includes(d), `${d} missing from ${csp}`);
  assert.doesNotMatch(csp, /unsafe-inline|unsafe-eval|'self'/);
  const meta = html.indexOf(`<meta http-equiv="Content-Security-Policy" content="${csp}" />`);
  assert.ok(meta > html.indexOf('<meta charset="UTF-8" />'), "policy must follow the charset");
  assert.ok(meta < html.indexOf("<script"), "policy must precede every script");
});

test("each chunk is an inert, compressed data block", async () => {
  const { html } = buildSingleFile(parts());
  for (const [name, text] of Object.entries(parts().chunks)) {
    const block = html.match(new RegExp(`<script type="application/json" id="whykit-chunk-${name}">([^<]*)</script>`))?.[1];
    assert.ok(block, `no data block for ${name}`);
    assert.equal(await decodeChunk(block), text);
  }
  assert.ok(html.indexOf("whykit-chunk-bodies") > html.indexOf('<div id="root">'), "data blocks sit in the body");
});

test("a page that does not reference the bundle is refused rather than half inlined", () => {
  assert.throws(() => buildSingleFile({ ...parts(), scriptFile: "assets/other.js" }), /does not load assets\/other\.js/);
  assert.throws(() => buildSingleFile({ ...parts(), styleFile: "assets/other.css" }), /does not load assets\/other\.css/);
  assert.throws(() => buildSingleFile({ ...parts(), style: "a{}</style><script>x()</script>" }), /style/);
  assert.throws(() => buildSingleFile({ ...parts(), style: "a{background:url(./assets/x.png)}" }), /loads another file/);
  assert.throws(() => buildSingleFile({ ...parts(), script: 'const a = "<!--<script>";' }), /<!--/);
  assert.throws(() => buildSingleFile({ ...parts(), html: HTML.replace("</head>", '<link rel="icon" href="./favicon.svg"></head>') }), /still references another file/);
});
