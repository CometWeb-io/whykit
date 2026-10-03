import { expect, test } from "@playwright/test";
import { open, seriousViolations } from "./helpers.ts";
import { NODE_W } from "../src/lib/graph.ts";

test("the evidence filter is in the URL and survives a reload", async ({ page }) => {
  await open(page, "#view=evidence");
  await page.getByRole("searchbox", { name: "Filter evidence" }).fill("hubspot report");
  await expect(page).toHaveURL(/#view=evidence&q=hubspot%20report$/);
  await page.reload();
  await expect(page.getByRole("searchbox", { name: "Filter evidence" })).toHaveValue("hubspot report");
  await expect(page.getByRole("status").filter({ hasText: /rows match/ })).toBeVisible();
});

test("the search dialog and its query reopen from a link", async ({ page }) => {
  await open(page, "#view=health");
  await page.keyboard.press("ControlOrMeta+k");
  await page.keyboard.type("hubspot");
  await expect(page).toHaveURL(/#view=health&search=hubspot$/);
  await page.reload();
  const dialog = page.getByRole("dialog", { name: "Search vault" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("combobox")).toHaveValue("hubspot");
  await expect(dialog.getByRole("option").first()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(/#view=health$/);
});

test("graph selection and workstream are in the URL and survive a reload", async ({ page }) => {
  await open(page, "#view=graph");
  const node = page.locator("svg g.graph-node[data-id='06-decisions/d-010-direct']");
  await node.click();
  await expect(page).toHaveURL(/node=06-decisions%2Fd-010-direct/);
  const inspector = page.getByRole("region", { name: "Selected note" });
  await expect(inspector).toContainText("06-decisions/d-010-direct");
  await page.reload();
  await expect(node).toHaveClass(/selected/);
  await expect(inspector).toContainText("Direct to ops directors");
  await inspector.getByRole("link", { name: "Open note" }).click();
  await expect(page.locator("main h1")).toContainText("Direct to ops directors");
  await page.goBack();
  await expect(page.locator("main h1")).toHaveText("Knowledge graph");

  await page.getByLabel("Workstream").selectOption("06-decisions");
  await expect(page).toHaveURL(/ws=06-decisions/);
  await page.reload();
  const ids = await page.locator("svg g.graph-node").evaluateAll(els => els.map(el => el.getAttribute("data-id") ?? ""));
  expect(ids.length).toBeGreaterThan(5);
  expect(ids.every(id => id.startsWith("06-decisions/"))).toBe(true);
  expect(await seriousViolations(page)).toEqual([]);
});

test("graph labels use the width they have and tooltips carry the full title", async ({ page }) => {
  await open(page, "#view=graph");
  const labels = await page.locator("svg g.graph-node").evaluateAll(els => els.map(el => ({
    title: el.getAttribute("aria-label")?.replace(/, \d+ links?$/, "") ?? "",
    tooltip: el.querySelector("title")?.textContent ?? "",
    lines: [...el.querySelectorAll("tspan")].map(t => ({ text: t.textContent ?? "", width: (t as SVGTextContentElement).getComputedTextLength() })),
  })));
  let longShownWhole = 0;
  for (const { title, tooltip, lines } of labels) {
    expect(tooltip.startsWith(title)).toBe(true);
    expect(lines.length).toBeLessThanOrEqual(2);
    for (const line of lines) expect(line.width, line.text).toBeLessThanOrEqual(NODE_W - 18 + 0.5);
    const shown = lines.map(l => l.text).join(" ");
    if (shown.endsWith("…")) expect(title.replace(/\s+/g, " ").startsWith(shown.slice(0, -1).trimEnd()), shown).toBe(true);
    else expect(shown).toBe(title.trim().replace(/\s+/g, " "));
    if (title.length > 26 && !shown.endsWith("…")) longShownWhole++;
  }
  // The old fixed 25-character cut truncated every one of these.
  expect(longShownWhole).toBeGreaterThan(5);
});
