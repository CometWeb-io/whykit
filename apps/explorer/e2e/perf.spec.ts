import { expect, test, type Page } from "@playwright/test";
import { SYNTHETIC_URL } from "./ports.ts";

// Budgets for the 5,000-note synthetic vault (tests/synthetic_vault.py). They
// are several times the cost measured on a laptop so a slow CI runner does not
// flake; they exist to catch a return of whole-vault rendering or quadratic
// work, which costs seconds rather than milliseconds at this size.
//
// Time to interactive was 760–970 ms on a laptop while the whole 10 MB index
// was bundled; with note bodies split into a chunk loaded after first paint it
// is 320–360 ms, so its budget dropped from 4,000 to 1,500 ms. `docText` is a
// cold deep link to a note, which has to wait for that chunk.
const BUDGET = { interactive: 1500, docText: 2500, search: 250, view: 1000, graphFocus: 250 };

test.describe.configure({ mode: "serial" });

/** Milliseconds from navigation start until the page has rendered and is idle. */
async function timeToInteractive(page: Page, hash = ""): Promise<number> {
  await page.goto(`${SYNTHETIC_URL}${hash}`);
  await expect(page.locator("main h1")).toBeVisible();
  // A frame after first paint plus an idle callback means React is no longer
  // blocking the main thread with the initial render.
  return page.evaluate(() => new Promise<number>(done => {
    requestAnimationFrame(() => requestIdleCallback(() => done(performance.now()), { timeout: 10_000 }));
  }));
}

/** Milliseconds from navigation start until a deep-linked note shows its text. */
async function timeToText(page: Page, hash: string): Promise<number> {
  // A hash-only goto would stay in the same document; leave it first so this is a cold load.
  await page.goto("about:blank");
  await page.goto(`${SYNTHETIC_URL}${hash}`);
  await expect(page.locator("main .markdown p").first()).toBeVisible();
  return page.evaluate(() => new Promise<number>(done => requestAnimationFrame(() => done(performance.now()))));
}

/** Milliseconds from an input event until the next painted frame shows `selector` text changed. */
async function inputLatency(page: Page, inputSelector: string, value: string, statusSelector: string): Promise<number> {
  return page.evaluate(async ({ inputSelector, value, statusSelector }) => {
    const input = document.querySelector<HTMLInputElement>(inputSelector);
    const status = document.querySelector(statusSelector);
    if (!input || !status) throw new Error("missing input or status");
    const before = status.textContent;
    // React tracks `input.value`; the prototype setter bypasses that so onChange fires.
    // eslint-disable-next-line @typescript-eslint/unbound-method
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    const t0 = performance.now();
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    for (;;) {
      await new Promise(r => requestAnimationFrame(r));
      if (status.textContent !== before) return performance.now() - t0;
      if (performance.now() - t0 > 10_000) throw new Error("status never changed");
    }
  }, { inputSelector, value, statusSelector });
}

/** Milliseconds from a sidebar click until the new view's heading is painted. */
async function viewSwitch(page: Page, label: string, h1: string): Promise<number> {
  return page.evaluate(async ({ label, h1 }) => {
    const button = [...document.querySelectorAll<HTMLButtonElement>("#sidebar nav button")].find(b => b.textContent?.trim() === label);
    if (!button) throw new Error(`no nav button ${label}`);
    const t0 = performance.now();
    button.click();
    for (;;) {
      await new Promise(r => requestAnimationFrame(r));
      if (document.querySelector("main h1")?.textContent === h1) {
        await new Promise(r => requestAnimationFrame(r));
        return performance.now() - t0;
      }
      if (performance.now() - t0 > 20_000) throw new Error(`${label} never rendered`);
    }
  }, { label, h1 });
}

/** Milliseconds from focusing a graph node until the highlight is painted. */
async function graphFocus(page: Page, index: number): Promise<number> {
  return page.evaluate(async index => {
    const node = document.querySelectorAll<SVGGElement>("svg g.graph-node")[index];
    if (!node) throw new Error("no graph node");
    const t0 = performance.now();
    node.focus();
    for (;;) {
      await new Promise(r => requestAnimationFrame(r));
      if (node.classList.contains("current")) {
        await new Promise(r => requestAnimationFrame(r));
        return performance.now() - t0;
      }
      if (performance.now() - t0 > 20_000) throw new Error("focus never highlighted");
    }
  }, index);
}

test("a 5,000-note vault loads, searches and navigates within budget", async ({ page }, info) => {
  const results: Record<string, number> = {};
  results.interactive = await timeToInteractive(page);
  expect(await page.locator("main").textContent()).toContain("Synthetic");

  await page.getByRole("button", { name: "Search vault" }).click();
  results.searchFirst = await inputLatency(page, "[role=combobox]", "pipeline", ".palette");
  results.searchNarrow = await inputLatency(page, "[role=combobox]", "pipeline renewal", ".palette");
  await page.keyboard.press("Escape");

  for (const [label, h1] of [
    ["Decisions", "Decisions"], ["Evidence", "Evidence"], ["Reviews", "Reviews"],
    ["Graph", "Knowledge graph"], ["Health", "Health"], ["Home", "synthetic"],
  ] as const) {
    results[`view:${label}`] = await viewSwitch(page, label, h1);
    if (label === "Graph") results.graphFocus = await graphFocus(page, 40);
  }

  results.docText = await timeToText(page, "#doc=06-decisions%2Fd-002-record");

  info.annotations.push({ type: "perf", description: JSON.stringify(results) });
  console.log(`perf (5k notes): ${JSON.stringify(Object.fromEntries(Object.entries(results).map(([k, v]) => [k, Math.round(v)])))}`);

  expect(results.interactive).toBeLessThan(BUDGET.interactive);
  expect(results.docText).toBeLessThan(BUDGET.docText);
  expect(results.searchFirst).toBeLessThan(BUDGET.search);
  expect(results.searchNarrow).toBeLessThan(BUDGET.search);
  expect(results.graphFocus).toBeLessThan(BUDGET.graphFocus);
  for (const [key, value] of Object.entries(results)) {
    if (key.startsWith("view:")) expect(value, key).toBeLessThan(BUDGET.view);
  }
});
