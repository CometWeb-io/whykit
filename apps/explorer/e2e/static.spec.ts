import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { open, seriousViolations } from "./helpers.ts";
import { EMPTY_URL, SYNTHETIC_URL } from "./ports.ts";

// The production build as a static site: a strict Content Security Policy,
// relative URLs that survive a sub-path, the note bodies in their own chunk,
// and a warning whenever the build contains confidential or restricted notes.

const BUILD = resolve(import.meta.dirname, ".build/northline");
const html = () => readFileSync(join(BUILD, "index.html"), "utf8");

/** Collect CSP violations from the first script the page runs. */
async function watchCsp(page: Page): Promise<() => Promise<string[]>> {
  await page.addInitScript(() => {
    const seen: string[] = [];
    (window as unknown as { __csp: string[] }).__csp = seen;
    document.addEventListener("securitypolicyviolation", e => seen.push(`${e.violatedDirective} ${e.blockedURI}`));
  });
  return () => page.evaluate(() => (window as unknown as { __csp?: string[] }).__csp ?? []);
}

/**
 * Script tags that do not load a bundled file. HTML tag and attribute names
 * are case-insensitive, so `<SCRIPT>` must be caught as well as `<script>`.
 */
function unbundledScripts(page: string): string[] {
  return (page.match(/<script\b[^>]*>/gi) ?? []).filter(tag => !/\ssrc\s*=\s*"\.\//i.test(tag));
}

function hasStyleElement(page: string): boolean {
  return /<style\b/i.test(page);
}

test("the inline-script check sees tags in any letter case", () => {
  expect(unbundledScripts('<SCRIPT>alert(1)</SCRIPT><Script type="module">x()</Script>')).toHaveLength(2);
  expect(unbundledScripts('<script type="module" crossorigin src="./assets/index.js"></script>')).toEqual([]);
  expect(hasStyleElement("<STYLE>body{}</STYLE>")).toBe(true);
});

test("the build ships a strict CSP and no inline script or style", () => {
  const page = html();
  const csp = page.match(/<meta http-equiv="Content-Security-Policy" content="([^"]+)"/)?.[1] ?? "";
  expect(csp).toContain("script-src 'self'");
  expect(csp).toContain("style-src 'self'");
  expect(csp).toContain("object-src 'none'");
  expect(csp).not.toContain("unsafe-inline");
  expect(csp).not.toContain("unsafe-eval");
  expect(page.indexOf("Content-Security-Policy")).toBeLessThan(page.search(/<script\b/i));
  expect(unbundledScripts(page)).toEqual([]);
  expect(hasStyleElement(page)).toBe(false);
  expect(page).not.toMatch(/\s(src|href)="\//i);
});

test("every view renders under the CSP without a violation", async ({ page }) => {
  const violations = await watchCsp(page);
  for (const hash of ["", "#view=graph", "#view=timeline", "#view=freshness", "#view=health", "#doc=06-decisions%2Fd-010-direct"]) {
    await open(page, hash);
  }
  await page.keyboard.press("ControlOrMeta+k");
  await page.keyboard.type("pricing");
  await expect(page.getByRole("option").first()).toBeVisible();
  expect(await violations()).toEqual([]);
});

test("the note bodies are a separate file, not part of the entry bundle", () => {
  const entry = html().match(/<script[^>]+src="\.\/(assets\/[^"]+\.js)"/i)?.[1];
  expect(entry).toBeTruthy();
  const assets = readdirSync(join(BUILD, "assets"));
  const chunk = assets.find(f => /^bodies-.*\.json$/.test(f));
  expect(chunk, `no bodies chunk among ${assets.join(", ")}`).toBeTruthy();
  // A sentence that only exists in a note body.
  const body = JSON.parse(readFileSync(resolve(import.meta.dirname, "../src/generated/bodies.json"), "utf8")) as Record<string, string>;
  const probe = (body["06-decisions/d-010-direct"] ?? "").split("\n").find(line => line.length > 60 && !line.startsWith("#") && !line.includes("|"));
  expect(probe).toBeTruthy();
  const needle = probe!.slice(0, 40);
  expect(readFileSync(join(BUILD, entry!), "utf8")).not.toContain(needle);
  expect(readFileSync(join(BUILD, "assets", chunk!), "utf8")).toContain(needle);
});

test("the build works from a sub-path, including the lazily loaded note text", async ({ page }) => {
  const prefix = "http://explorer.test/teams/ledger/explorer/";
  const outside: string[] = [];
  await page.route("**/*", async route => {
    const url = route.request().url();
    if (!url.startsWith(prefix)) { outside.push(url); return route.abort(); }
    const rel = decodeURIComponent(new URL(url).pathname.slice(new URL(prefix).pathname.length)) || "index.html";
    const file = join(BUILD, rel);
    if (!file.startsWith(BUILD) || !existsSync(file)) return route.fulfill({ status: 404, body: "not found" });
    return route.fulfill({ path: file });
  });
  const violations = await watchCsp(page);
  await page.goto(`${prefix}#doc=06-decisions%2Fd-010-direct`);
  await expect(page.locator("main h1")).toBeVisible();
  await expect(page.locator("main .markdown")).toBeVisible();
  await expect(page.locator("main .markdown p").first()).not.toBeEmpty();
  await page.locator("#sidebar nav").getByRole("button", { name: "Graph", exact: true }).click();
  await expect(page.locator("main h1")).toHaveText("Knowledge graph");
  expect(outside).toEqual([]);
  expect(await violations()).toEqual([]);
});

test("a build with confidential or restricted notes says so on every page", async ({ page }) => {
  for (const hash of ["#view=graph", "#view=evidence", ""]) {
    await page.goto(`${SYNTHETIC_URL}${hash}`);
    await expect(page.locator("main h1")).toBeVisible();
    const note = page.getByRole("note", { name: "Sensitive content in this build" });
    await expect(note).toBeVisible();
    await expect(note).toContainText(/This build includes \d[\d,]* confidential/);
    await expect(note).toContainText("no access control");
  }
  expect(await seriousViolations(page)).toEqual([]);
});

test("a build without them shows no warning", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("note", { name: "Sensitive content in this build" })).toHaveCount(0);
  await open(page, "", EMPTY_URL);
  await expect(page.getByRole("note", { name: "Sensitive content in this build" })).toHaveCount(0);
});

test("private evidence keeps its row classification visible in the private viewer", async ({ page }) => {
  await page.goto(`${SYNTHETIC_URL}#view=evidence`);
  const table = page.getByRole("region", { name: "Evidence register" });
  await expect(table.getByRole("columnheader", { name: "Sensitivity", exact: true })).toBeVisible();
  const row = table.getByRole("row").filter({ has: page.getByRole("cell", { name: "E-001", exact: true }) });
  await expect(row.getByText("restricted", { exact: true })).toBeVisible();
  await page.getByRole("searchbox", { name: "Filter evidence" }).fill("restricted");
  await expect(row).toBeVisible();
});

test("the lint findings are a separate file, loaded only by Health", async ({ page }) => {
  const build = resolve(import.meta.dirname, ".build/synthetic");
  const entry = readFileSync(join(build, "index.html"), "utf8").match(/<script[^>]+src="\.\/(assets\/[^"]+\.js)"/i)?.[1];
  expect(entry).toBeTruthy();
  const chunk = readdirSync(join(build, "assets")).find(f => /^findings-.*\.json$/.test(f));
  expect(chunk, "no findings chunk").toBeTruthy();
  const findings = JSON.parse(readFileSync(join(build, "assets", chunk!), "utf8")) as { path: string; message: string }[];
  expect(findings.length).toBeGreaterThan(1000);
  const code = readFileSync(join(build, entry!), "utf8");
  expect(code).not.toContain(findings[0]!.message);
  expect(code).not.toContain('"findings":[');

  const fetched: string[] = [];
  page.on("request", req => { if (/findings-[^/]*\.json$/.test(req.url())) fetched.push(req.url()); });
  await page.goto(`${SYNTHETIC_URL}#view=decisions`);
  await expect(page.locator("main h1")).toHaveText("Decisions");
  await page.waitForTimeout(300);
  expect(fetched, "findings fetched before Health asked for them").toEqual([]);
  await page.locator("#sidebar nav").getByRole("button", { name: "Health", exact: true }).click();
  await expect(page.locator(".findings .finding").first()).toBeVisible();
  await expect(page.locator(".show-more")).toContainText(`Showing 200 of ${findings.length} findings`);
  expect(fetched).toHaveLength(1);
});

test("a deep link to Health shows the findings", async ({ page }) => {
  await page.goto(`${SYNTHETIC_URL}#view=health`);
  await expect(page.locator(".findings .finding").first()).toBeVisible();
});
