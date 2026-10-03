import { expect, test, type Page } from "@playwright/test";
import { open } from "./helpers.ts";

const CHAIN = ["D-009", "D-010", "D-011"];

async function chainIds(page: Page): Promise<string[]> {
  return page.locator(".lineage .chain-step .decision-id").allTextContents();
}

test("a superseded decision shows its whole chain and points at the current one", async ({ page }) => {
  await open(page, "#doc=06-decisions%2Fd-010-direct");
  const lineage = page.getByRole("region", { name: "Supersession chain" });
  await expect(lineage).toBeVisible();
  expect(await chainIds(page)).toEqual(CHAIN);
  await expect(lineage.locator('[aria-current="step"]')).toContainText("D-010");
  await expect(lineage.locator('[aria-current="step"] a')).toHaveCount(0);
  await expect(lineage.getByText("D-010 is no longer current.")).toBeVisible();
  await expect(lineage.locator(".chain-step").nth(1)).toContainText("supersedes D-009");
  await expect(lineage.locator(".chain-step").nth(2)).toContainText("supersedes D-010");

  await lineage.getByRole("link", { name: /^D-011: / }).click();
  await expect(page.locator("main h1")).toContainText("D-011");
  expect(await chainIds(page)).toEqual(CHAIN);
  await expect(page.locator('.lineage [aria-current="step"]')).toContainText("D-011");
  await expect(page.locator(".lineage .notice")).toHaveCount(0);
});

test("the chain is the same from its root and lets you walk forward", async ({ page }) => {
  await open(page, "#doc=06-decisions%2Fd-009-partner-led");
  expect(await chainIds(page)).toEqual(CHAIN);
  await page.locator(".lineage .chain-step").nth(1).getByRole("link").click();
  await expect(page.locator("main h1")).toContainText("D-010");
});

test("a decision that was never superseded shows no chain", async ({ page }) => {
  await open(page, "#doc=06-decisions%2Fd-007-hubspot");
  await expect(page.locator(".lineage")).toHaveCount(0);
});

test("the decisions timeline is newest first and records supersession", async ({ page }) => {
  await open(page, "#view=decisions");
  const items = page.locator(".timeline li");
  await expect(items.first()).toContainText("D-011");
  await expect(items.first()).toContainText("supersedes D-010");
  const row = (id: string) => page.locator(".timeline li").filter({ has: page.locator(".decision-id", { hasText: new RegExp(`^${id}$`) }) });
  await expect(row("D-010")).toContainText("supersedes D-009 · superseded by D-011");
  await expect(row("D-009")).toContainText("superseded by D-010");
  await row("D-009").getByRole("button").click();
  await expect(page.locator("main h1")).toContainText("D-009");
});
