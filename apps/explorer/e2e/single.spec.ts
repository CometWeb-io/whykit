import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import { seriousViolations } from "./helpers.ts";

// `npm run build:single`: the whole Explorer in one HTML file that works when
// opened straight from disk, where browsers neither run module scripts loaded
// from other files nor allow fetch().

const BUILD = resolve(import.meta.dirname, ".build/single");
const FILE = pathToFileURL(join(BUILD, "index.html")).href;
const html = () => readFileSync(join(BUILD, "index.html"), "utf8");

/** Collect CSP violations and every request that is not this one file. */
async function watch(page: Page): Promise<{ violations: () => Promise<string[]>; requests: string[]; errors: string[] }> {
  const requests: string[] = [];
  const errors: string[] = [];
  page.on("request", req => { if (!req.url().startsWith(FILE)) requests.push(req.url()); });
  page.on("console", msg => { if (msg.type() === "error") errors.push(msg.text()); });
  page.on("pageerror", err => errors.push(err.message));
  await page.addInitScript(() => {
    const seen: string[] = [];
    (window as unknown as { __csp: string[] }).__csp = seen;
    document.addEventListener("securitypolicyviolation", e => seen.push(`${e.violatedDirective} ${e.blockedURI}`));
  });
  return { violations: () => page.evaluate(() => (window as unknown as { __csp?: string[] }).__csp ?? []), requests, errors };
}

async function openFile(page: Page, hash = ""): Promise<void> {
  await page.goto(`${FILE}${hash}`);
  await expect(page.locator("main h1")).toBeVisible();
}

test("the build is one file that loads nothing else", () => {
  expect(readdirSync(BUILD)).toEqual(["index.html"]);
  const page = html();
  expect(page).not.toMatch(/<(?:script|link|img)\b[^>]*\s(?:src|href)\s*=/i);
  const csp = page.match(/<meta http-equiv="Content-Security-Policy" content="([^"]+)"/)?.[1] ?? "";
  expect(csp).toMatch(/script-src 'sha256-[A-Za-z0-9+/]+=*'(;|$)/);
  expect(csp).toMatch(/style-src 'sha256-[A-Za-z0-9+/]+=*'(;|$)/);
  expect(csp).toContain("connect-src 'none'");
  expect(csp).not.toContain("unsafe-inline");
  expect(csp).not.toContain("unsafe-eval");
  expect(page.indexOf("Content-Security-Policy")).toBeLessThan(page.search(/<script\b/i));
});

test("it works from file://, every view, under its policy", async ({ page }) => {
  const seen = await watch(page);
  await openFile(page);
  await expect(page.locator("main h1")).toHaveText("WhyKit — Northline example vault");
  for (const [label, h1] of [["Decisions", "Decisions"], ["Timeline", "Decision timeline"], ["Evidence", "Evidence"], ["Freshness", "Evidence freshness"], ["Reviews", "Reviews"], ["Graph", "Knowledge graph"], ["Health", "Health"]] as const) {
    await page.locator("#sidebar nav").getByRole("button", { name: label, exact: true }).first().click();
    await expect(page.locator("main h1")).toHaveText(h1);
  }
  await expect(page.locator("main")).not.toContainText("Loading");
  expect(await seriousViolations(page)).toEqual([]);
  expect(await seen.violations()).toEqual([]);
  expect(seen.requests).toEqual([]);
  expect(seen.errors).toEqual([]);
});

test("a deep link to a note shows its text and supersession chain from the embedded data", async ({ page }) => {
  const seen = await watch(page);
  await openFile(page, "#doc=06-decisions%2Fd-010-direct");
  await expect(page.locator("main .markdown p").first()).toBeVisible();
  await expect(page.locator(".chain .chain-step")).toHaveCount(3);
  await page.locator(".chain a.chain-card").first().click();
  await expect(page).toHaveURL(/#doc=06-decisions%2Fd-009/);
  expect(await seen.violations()).toEqual([]);
  expect(seen.requests).toEqual([]);
});

test("search covers note text, which only the embedded bodies contain", async ({ page }) => {
  const bodies = JSON.parse(readFileSync(resolve(import.meta.dirname, "../src/generated/bodies.json"), "utf8")) as Record<string, string>;
  // A phrase from a note body that is not in any title, id or summary.
  const probe = (bodies["06-decisions/d-010-direct"] ?? "").split("\n").find(line => line.length > 60 && !line.startsWith("#") && !line.includes("|") && !line.includes("[["));
  expect(probe).toBeTruthy();
  const phrase = probe!.replace(/[*_`>]/g, "").trim().split(/\s+/).slice(2, 6).join(" ");
  await openFile(page);
  await page.keyboard.press("ControlOrMeta+k");
  await page.keyboard.type(phrase);
  await expect(page.getByRole("option").first()).toBeVisible();
  await expect(page.locator(".palette-status")).not.toContainText("Still loading");
});
