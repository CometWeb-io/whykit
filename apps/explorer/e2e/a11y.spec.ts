import { expect, test } from "@playwright/test";
import { open, PAGES, seriousViolations } from "./helpers.ts";
import { EMPTY_URL } from "./ports.ts";

const EXTRA = ["#doc=06-decisions%2Fd-010-direct", "#doc=00-context%2Fevidence-register", "#doc=does-not-exist"];

for (const hash of [...PAGES.map(p => p.hash), ...EXTRA]) {
  test(`no serious or critical axe violations: ${hash || "home"}`, async ({ page }) => {
    await open(page, hash);
    expect(await seriousViolations(page)).toEqual([]);
  });
}

test("no serious or critical axe violations with the search dialog open", async ({ page }) => {
  await open(page);
  await page.keyboard.press("ControlOrMeta+k");
  await page.keyboard.type("decision");
  await expect(page.getByRole("option").first()).toBeVisible();
  expect(await seriousViolations(page)).toEqual([]);
});

for (const hash of ["", "#view=decisions", "#view=evidence", "#view=reviews"]) {
  test(`no serious or critical axe violations on an empty vault: ${hash || "home"}`, async ({ page }) => {
    await open(page, hash, EMPTY_URL);
    expect(await seriousViolations(page)).toEqual([]);
  });
}
