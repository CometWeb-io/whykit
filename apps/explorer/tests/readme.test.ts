import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { CSP, CSP_HEADER } from "../src/lib/csp.ts";

// The README tells people which headers to send. Copied by hand, it would
// drift from the policy the build actually ships.
const README = readFileSync(new URL("../README.md", import.meta.url), "utf8");

function section(heading: string): string {
  const start = README.indexOf(`\n${heading}\n`);
  assert.ok(start >= 0, `README has no "${heading}" section`);
  const rest = README.slice(start + heading.length + 2);
  const level = heading.match(/^#+/)![0];
  const next = rest.search(new RegExp(`\\n#{1,${level.length}} `));
  return next >= 0 ? rest.slice(0, next) : rest;
}

test("the README quotes the shipped policy exactly", () => {
  assert.ok(README.includes(CSP), "the <meta> policy in the README differs from src/lib/csp.ts");
});

test("every host recipe sends the shipped policy with frame-ancestors", () => {
  const headers = section("### Security headers");
  for (const host of ["GitHub Pages", "Netlify", "Cloudflare Pages", "nginx"]) assert.ok(headers.includes(host), `no advice for ${host}`);
  const recipes = [...headers.matchAll(/```[a-z]*\n([\s\S]*?)```/g)].map(m => m[1]!);
  assert.ok(recipes.length >= 3, "expected a recipe per configurable host");
  for (const recipe of recipes) {
    assert.ok(recipe.includes(CSP_HEADER), `a recipe does not send the shipped policy with frame-ancestors:\n${recipe}`);
    assert.match(recipe, /X-Content-Type-Options\W+nosniff/);
  }
});

test("the README documents the single-file build", () => {
  const single = section("### One file that opens from disk");
  assert.match(single, /npm run build:single/);
  assert.match(single, /file:\/\//);
});
