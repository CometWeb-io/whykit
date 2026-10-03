import { expect, test } from "@playwright/test";
import { open } from "./helpers.ts";

test("graph nodes are reachable and operable from the keyboard", async ({ page }) => {
  await open(page, "#view=graph");
  const nodes = page.locator("svg g.graph-node");
  expect(await nodes.count()).toBeGreaterThan(20);
  await expect(page.locator("svg[aria-label^='Knowledge graph:']")).toBeVisible();

  // Tab from the heading area into the first node, then on to the second.
  await page.locator("main").focus();
  let guard = 0;
  while (!(await nodes.first().evaluate(el => el === document.activeElement)) && guard++ < 40) {
    await page.keyboard.press("Tab");
  }
  await expect(nodes.first()).toBeFocused();
  await expect(nodes.first()).toHaveClass(/current/);
  const label = (await nodes.first().getAttribute("aria-label")) ?? "";
  expect(label).toMatch(/, \d+ links?$/);

  // Focus dims unrelated nodes and keeps neighbours lit.
  const dimmed = await nodes.evaluateAll(els => els.filter(el => getComputedStyle(el).opacity === "0.25").length);
  expect(dimmed).toBeGreaterThan(0);

  await page.keyboard.press("Tab");
  await expect(nodes.nth(1)).toBeFocused();
  await expect(nodes.first()).not.toHaveClass(/current/);

  const title = label.replace(/, \d+ links?$/, "");
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Enter");
  await expect(page.locator("main h1")).toHaveText(title);
});

test("Space also opens a focused graph node", async ({ page }) => {
  await open(page, "#view=graph");
  const node = page.locator("svg g.graph-node").nth(3);
  await node.focus();
  const title = ((await node.getAttribute("aria-label")) ?? "").replace(/, \d+ links?$/, "");
  await page.keyboard.press("Space");
  await expect(page.locator("main h1")).toHaveText(title);
});
