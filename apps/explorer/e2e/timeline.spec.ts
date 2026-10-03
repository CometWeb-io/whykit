import { expect, test } from "@playwright/test";
import { open, seriousViolations } from "./helpers.ts";
import { EMPTY_URL } from "./ports.ts";

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-09-17T12:00:00Z"));
});

test("each decision shows the period it was in force", async ({ page }) => {
  await open(page, "#view=timeline");
  const rows = page.locator(".lifespan");
  await expect(rows).toHaveCount(11);
  const row = (id: string) => rows.filter({ has: page.getByRole("link", { name: new RegExp(`^${id} `) }) });
  const d010 = row("D-010");
  await expect(d010).toContainText("2026-08-11 → 2026-09-15 · in force 35 days");
  await expect(d010.getByRole("link", { name: "D-011" })).toBeVisible();
  await expect(row("D-011")).toContainText("2026-09-15 → today · 2 days");
  // Newest first by default.
  await expect(rows.first()).toContainText("D-011");
});

test("filters narrow the timeline, live in the URL and survive a reload", async ({ page }) => {
  await open(page, "#view=timeline");
  await page.getByLabel("Owner").selectOption("Lena Krüger");
  await page.getByLabel("Status").selectOption("superseded");
  await expect(page).toHaveURL(/owner=Lena%20Kr%C3%BCger/);
  await expect(page).toHaveURL(/status=superseded/);
  await expect(page.getByRole("status").filter({ hasText: /match/ })).toHaveText("2 of 11 decisions match");
  await page.reload();
  await expect(page.locator(".lifespan")).toHaveCount(2);
  await expect(page.getByLabel("Owner")).toHaveValue("Lena Krüger");
  await expect(page.getByLabel("Status")).toHaveValue("superseded");

  await page.getByLabel("Tag").selectOption("crm");
  await expect(page.getByText("No decisions match these filters.")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page.locator(".lifespan")).toHaveCount(11);
  await expect(page).toHaveURL(/#view=timeline$/);
});

test("filter changes replace history, so Back leaves the page", async ({ page }) => {
  await open(page, "#view=decisions");
  await page.getByRole("link", { name: /stayed in force/ }).click();
  await expect(page.locator("main h1")).toHaveText("Decision timeline");
  await page.getByLabel("Order").selectOption("oldest");
  await expect(page.locator(".lifespan").first()).toContainText("D-007");
  await page.goBack();
  await expect(page.locator("main h1")).toHaveText("Decisions");
});

test("a timeline row opens its decision record", async ({ page }) => {
  await open(page, "#view=timeline&tag=crm");
  await page.locator(".lifespan a").first().click();
  await expect(page.locator("main h1")).toContainText("HubSpot");
});

test("an empty vault explains the missing history", async ({ page }) => {
  await open(page, "#view=timeline", EMPTY_URL);
  await expect(page.getByText("The decision log is empty")).toBeVisible();
  expect(await seriousViolations(page)).toEqual([]);
});

test("no serious or critical axe violations on a filtered timeline", async ({ page }) => {
  await open(page, "#view=timeline&owner=Maya%20Chen&sort=oldest");
  expect(await seriousViolations(page)).toEqual([]);
});
