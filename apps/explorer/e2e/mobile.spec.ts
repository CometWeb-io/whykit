import { expect, test, type Page } from "@playwright/test";
import { open, PAGES, seriousViolations } from "./helpers.ts";

async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

test("the navigation drawer opens, navigates and closes", async ({ page }) => {
  await open(page);
  const toggle = page.getByRole("button", { name: "Open navigation" });
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  const nav = page.getByRole("navigation", { name: "Vault" });
  await expect(nav).toBeInViewport();
  await nav.getByRole("button", { name: "Evidence", exact: true }).click();
  await expect(page.locator("main h1")).toHaveText("Evidence");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(nav).not.toBeInViewport();
});

test("Escape and the close button both dismiss the drawer", async ({ page }) => {
  await open(page);
  const toggle = page.getByRole("button", { name: "Open navigation" });
  await toggle.click();
  await page.keyboard.press("Escape");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await page.locator(".close-nav").click();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
});

test("no page scrolls horizontally on a phone", async ({ page }) => {
  for (const { hash } of [...PAGES, { hash: "#doc=06-decisions%2Fd-010-direct" }]) {
    await open(page, hash);
    expect(await horizontalOverflow(page), hash || "home").toBeLessThanOrEqual(0);
  }
});

test("search opens from the header button on touch devices", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Search vault" }).tap();
  await expect(page.getByRole("dialog", { name: "Search vault" })).toBeVisible();
  await page.getByRole("combobox").fill("hubspot");
  await page.getByRole("option").first().tap();
  await expect(page.getByRole("dialog")).toBeHidden();
});

test("the supersession chain stacks on a phone", async ({ page }) => {
  await open(page, "#doc=06-decisions%2Fd-010-direct");
  const steps = page.locator(".lineage .chain-step");
  const boxes = await Promise.all([0, 1, 2].map(async i => (await steps.nth(i).boundingBox())?.y ?? 0));
  expect(boxes[0]).toBeLessThan(boxes[1] ?? 0);
  expect(boxes[1]).toBeLessThan(boxes[2] ?? 0);
});

for (const hash of ["", "#view=evidence", "#doc=06-decisions%2Fd-010-direct"]) {
  test(`no serious or critical axe violations on a phone: ${hash || "home"}`, async ({ page }) => {
    await open(page, hash);
    expect(await seriousViolations(page)).toEqual([]);
  });
}
