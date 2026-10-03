import { expect, test, type Page } from "@playwright/test";
import { open, seriousViolations } from "./helpers.ts";
import { SYNTHETIC_URL } from "./ports.ts";

const titleOf = (label: string | null) => (label ?? "").replace(/, \d+ links?$/, "");

/** Press Tab from the top of the page until a graph note has focus. */
async function tabIntoGraph(page: Page): Promise<number> {
  await page.locator("main").focus();
  for (let presses = 1; presses <= 40; presses++) {
    await page.keyboard.press("Tab");
    if (await page.evaluate(() => document.activeElement?.matches("g.graph-node") ?? false)) return presses;
  }
  throw new Error("Tab never reached the graph");
}

const focusedId = (page: Page) => page.evaluate(() => document.activeElement?.getAttribute("data-id") ?? null);

test("the graph is one listbox with a single Tab stop", async ({ page }) => {
  await open(page, "#view=graph");
  const graph = page.getByRole("listbox", { name: /^Knowledge graph:/ });
  await expect(graph).toBeVisible();
  const nodes = graph.locator("g.graph-node");
  expect(await nodes.count()).toBeGreaterThan(20);
  await expect(graph.getByRole("option")).toHaveCount(await nodes.count());
  // Exactly one option is in the Tab order.
  expect(await page.locator("g.graph-node[tabindex='0']").count()).toBe(1);
  expect(await page.locator("g.graph-node[tabindex='-1']").count()).toBe((await nodes.count()) - 1);

  await tabIntoGraph(page);
  await expect(nodes.first()).toBeFocused();
  await expect(nodes.first()).toHaveClass(/current/);
  expect(await nodes.first().getAttribute("aria-label")).toMatch(/, \d+ links?$/);
  // Focus dims unrelated nodes and keeps neighbours lit.
  const dimmed = await nodes.evaluateAll(els => els.filter(el => getComputedStyle(el).opacity === "0.25").length);
  expect(dimmed).toBeGreaterThan(0);

  // One more Tab leaves the graph entirely instead of walking every note.
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.activeElement?.matches("g.graph-node") ?? false)).toBe(false);
});

test("arrow keys, Home, End and type-ahead move focus; the Tab stop follows it", async ({ page }) => {
  await open(page, "#view=graph");
  const nodes = page.locator("svg g.graph-node");
  const ids = await nodes.evaluateAll(els => els.map(el => el.getAttribute("data-id") ?? ""));
  await tabIntoGraph(page);

  await page.keyboard.press("ArrowDown");
  expect(await focusedId(page)).toBe(ids[1]);
  await expect(nodes.nth(1)).toHaveClass(/current/);
  await expect(nodes.nth(1)).toHaveAttribute("tabindex", "0");
  await expect(nodes.first()).toHaveAttribute("tabindex", "-1");
  await page.keyboard.press("ArrowUp");
  expect(await focusedId(page)).toBe(ids[0]);

  await page.keyboard.press("End");
  expect(await focusedId(page)).toBe(ids.at(-1));
  await page.keyboard.press("Home");
  expect(await focusedId(page)).toBe(ids[0]);

  // Right moves to the next column on the same row.
  const before = await nodes.first().boundingBox();
  await page.keyboard.press("ArrowRight");
  const after = await page.locator("g.graph-node:focus").boundingBox();
  expect(after!.x).toBeGreaterThan(before!.x);
  expect(Math.abs(after!.y - before!.y)).toBeLessThan(2);

  // Leaving and re-entering the graph returns to the note that had focus.
  await page.keyboard.press("ArrowDown");
  const remembered = await focusedId(page);
  await page.keyboard.press("Tab");
  await page.keyboard.press("Shift+Tab");
  expect(await focusedId(page)).toBe(remembered);

  // Type-ahead: typing the start of a title moves focus to that note.
  const titles = await nodes.evaluateAll(els => els.map(el => el.getAttribute("aria-label") ?? ""));
  const target = titles.findIndex((t, i) => i > 3 && /^[a-z]{4}/i.test(t) && titles.filter(o => o.toLowerCase().startsWith(t.slice(0, 4).toLowerCase())).length === 1);
  expect(target).toBeGreaterThan(0);
  await page.keyboard.type(titles[target]!.slice(0, 4).toLowerCase());
  expect(await focusedId(page)).toBe(ids[target]);
});

test("Space selects the focused note and Enter opens it", async ({ page }) => {
  await open(page, "#view=graph");
  const nodes = page.locator("svg g.graph-node");
  await tabIntoGraph(page);
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  const node = nodes.nth(2);
  const id = await node.getAttribute("data-id");
  await expect(node).toHaveAttribute("aria-selected", "false");
  await page.keyboard.press("Space");
  await expect(node).toHaveAttribute("aria-selected", "true");
  expect(new URL(page.url()).hash).toContain(`node=${encodeURIComponent(id!)}`);
  await expect(page.getByRole("region", { name: "Selected note" })).toContainText(titleOf(await node.getAttribute("aria-label")));

  const title = titleOf(await node.getAttribute("aria-label"));
  await page.keyboard.press("Enter");
  await expect(page.locator("main h1")).toHaveText(title);
});

test("a deep-linked selection is the graph's Tab stop", async ({ page }) => {
  await open(page, "#view=graph&node=06-decisions%2Fd-010-direct");
  await tabIntoGraph(page);
  expect(await focusedId(page)).toBe("06-decisions/d-010-direct");
});

test("the graph listbox passes axe with a note focused and selected", async ({ page }) => {
  await open(page, "#view=graph&node=06-decisions%2Fd-010-direct");
  await tabIntoGraph(page);
  await page.keyboard.press("ArrowDown");
  expect(await seriousViolations(page)).toEqual([]);
});

test("a 5,000-note graph is still one Tab stop", async ({ page }) => {
  await page.goto(`${SYNTHETIC_URL}#view=graph`);
  await expect(page.locator("main h1")).toHaveText("Knowledge graph");
  const total = await page.locator("svg g.graph-node").count();
  expect(total).toBeGreaterThan(4000);
  expect(await page.locator("svg g.graph-node[tabindex='0']").count()).toBe(1);
  const presses = await tabIntoGraph(page);
  await page.keyboard.press("End");
  const last = await page.locator("svg g.graph-node").last().getAttribute("data-id");
  expect(await focusedId(page)).toBe(last);
  // From the last note one Tab leaves the graph: nothing else in it is a stop.
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.activeElement?.matches("g.graph-node") ?? false)).toBe(false);
  expect(presses).toBeLessThan(40);
});
