import { expect, test } from "@playwright/test";
import { open, PAGES } from "./helpers.ts";

test("every primary view is reachable from the sidebar", async ({ page }) => {
  await open(page);
  const nav = page.getByRole("navigation", { name: "Vault" });
  for (const { nav: label, h1 } of PAGES.slice(1)) {
    // The primary entry comes first; the spine may hold a workstream of the same name.
    const button = nav.getByRole("button", { name: label, exact: true }).first();
    await button.click();
    await expect(page.locator("main h1")).toHaveText(h1);
    await expect(button).toHaveAttribute("aria-current", "page");
    await expect(page).toHaveTitle(new RegExp(`· WhyKit Explorer$`));
  }
  await page.goBack();
  await expect(page.locator("main h1")).toHaveText("Templates");
});

test("navigation moves focus to the new page and back to the top", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "View all" }).click();
  await expect(page.locator("main h1")).toHaveText("Decisions");
  await expect(page.locator("main")).toBeFocused();
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
});

test("deep links open documents and wikilinks navigate between them", async ({ page }) => {
  await open(page, "#doc=06-decisions%2Fd-007-hubspot");
  await expect(page.locator("main h1")).toContainText("HubSpot");
  await expect(page).toHaveTitle(/HubSpot.*· WhyKit Explorer/);
  await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toContainText("06-decisions/d-007-hubspot");
  const related = page.locator(".relations a").first();
  const target = await related.getAttribute("href");
  await related.click();
  await expect(page).toHaveURL(new RegExp(`${target?.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") ?? ""}$`));
});

test("an unknown document shows a recoverable not-found page", async ({ page }) => {
  await open(page, "#doc=does-not-exist");
  await expect(page.locator("main h1")).toHaveText("Document not found");
  await page.getByRole("link", { name: "Back home" }).click();
  await expect(page.locator("main h1")).toHaveText(PAGES[0].h1);
});

test("in-page anchors keep the current route", async ({ page }) => {
  await open(page, "#view=health");
  await page.evaluate(() => { location.hash = "content"; });
  await expect(page.locator("main h1")).toHaveText("Health");
});

test("the skip link moves focus to the main content", async ({ page }) => {
  await open(page);
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("main")).toBeFocused();
});

test("the evidence filter narrows rows and reports an empty match", async ({ page }) => {
  await open(page, "#view=evidence");
  const filter = page.getByRole("searchbox", { name: "Filter evidence" });
  await filter.fill("hubspot");
  await expect(page.getByRole("status").filter({ hasText: /rows match/ })).toContainText(/^\d+ of \d+ rows match$/);
  for (const row of await page.locator(".evidence-table tbody tr").all()) {
    await expect(row).toContainText(/hubspot/i);
  }
  await filter.fill("zzz-no-such-evidence");
  await expect(page.getByText("No evidence matches “zzz-no-such-evidence”.")).toBeVisible();
});
