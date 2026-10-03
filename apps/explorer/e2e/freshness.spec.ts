import { expect, test } from "@playwright/test";
import { open, seriousViolations } from "./helpers.ts";
import { EMPTY_URL, SYNTHETIC_URL } from "./ports.ts";

test.beforeEach(async ({ page }) => {
  // tests/synthetic_vault.py pins its data to this date.
  await page.clock.setFixedTime(new Date("2026-09-17T12:00:00Z"));
});

test("without windows the page says ages are informational", async ({ page }) => {
  await open(page, "#view=freshness");
  await expect(page.getByText("No access-age windows are configured")).toBeVisible();
  await expect(page.locator(".freshness-table .status-unchecked").first()).toHaveText("No policy");
  // E-000 is retired and replaced by E-010; nothing in force cites it.
  const retired = page.locator(".retired-list > li").filter({ hasText: "E-000" });
  await expect(retired).toContainText("replaced by E-010");
  await expect(retired).toContainText("No note in force cites it.");
});

test("policy windows from whykit.toml classify active evidence", async ({ page }) => {
  await open(page, "#view=freshness", SYNTHETIC_URL);
  await expect(page.locator(".policy-list")).toContainText("analytics120 days");
  await page.getByLabel("Freshness", { exact: true }).selectOption("stale");
  await expect(page).toHaveURL(/state=stale/);
  const rows = page.locator(".freshness-table tbody tr");
  expect(await rows.count()).toBeGreaterThan(10);
  for (const row of (await rows.all()).slice(0, 20)) {
    await expect(row.locator(".status")).toHaveText("Stale");
    const [age, window] = ((await row.locator(".age .mono").textContent()) ?? "").match(/\d+/g)!.map(Number);
    expect(age).toBeGreaterThan(window!);
  }
  await page.reload();
  await expect(page.getByLabel("Freshness", { exact: true })).toHaveValue("stale");
});

test("long registers render in pages", async ({ page }) => {
  await open(page, "#view=freshness", SYNTHETIC_URL);
  const rows = page.locator(".freshness-table tbody tr");
  await expect(rows).toHaveCount(100);
  await page.getByRole("button", { name: "Show 100 more" }).click();
  await expect(rows).toHaveCount(200);
  // A filter change starts again from the first page.
  await page.getByLabel("Type").selectOption("interview");
  expect(await rows.count()).toBeLessThanOrEqual(100);
});

test("retired evidence lists the notes in force that still cite it", async ({ page }) => {
  await open(page, "#view=freshness&retired=cited", SYNTHETIC_URL);
  await expect(page.getByLabel("Only sources still cited by notes in force")).toBeChecked();
  const first = page.locator(".retired-list > li").first();
  await expect(first).toContainText(/Still cited by \d+ notes? in force/);
  const link = first.locator(".citers a").first();
  const title = (await link.textContent()) ?? "";
  await link.click();
  await expect(page.locator("main h1")).toHaveText(title);
});

test("an empty vault has nothing to age", async ({ page }) => {
  await open(page, "#view=freshness", EMPTY_URL);
  await expect(page.getByText("The evidence register is empty")).toBeVisible();
  expect(await seriousViolations(page)).toEqual([]);
});

test("no serious or critical axe violations with policy and filters", async ({ page }) => {
  await open(page, "#view=freshness&state=due", SYNTHETIC_URL);
  expect(await seriousViolations(page)).toEqual([]);
});
