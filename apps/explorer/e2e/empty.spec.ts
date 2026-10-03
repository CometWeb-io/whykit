import { expect, test } from "@playwright/test";
import { open } from "./helpers.ts";
import { EMPTY_URL } from "./ports.ts";

// A fresh `whykit init --minimal` vault: no decisions, evidence or reviews yet.
test.use({ baseURL: EMPTY_URL });

test("home explains what is missing instead of rendering blank sections", async ({ page }) => {
  await open(page);
  await expect(page.getByText("No decisions recorded yet.")).toBeVisible();
  await expect(page.getByRole("button", { name: "View all" })).toHaveCount(0);
  // The template's own conventions are approved sources of truth from day one.
  await expect(page.getByRole("region", { name: "Current sources of truth" }).getByRole("button").first()).toBeVisible();
  // A fresh vault is not the synthetic example and must not be labelled as one.
  await expect(page.getByText("This is a synthetic example vault.")).toHaveCount(0);
});

for (const [hash, text] of [
  ["#view=decisions", "The decision log is empty."],
  ["#view=evidence", "The evidence register is empty."],
  ["#view=reviews", "No review events recorded yet."],
] as const) {
  test(`empty state on ${hash}`, async ({ page }) => {
    await open(page, hash);
    await expect(page.getByRole("status").filter({ hasText: text })).toBeVisible();
  });
}

test("health shows lint warnings for the unanswered agent contract", async ({ page }) => {
  await open(page, "#view=health");
  await expect(page.locator(".findings li").first()).toContainText("agents.unconfigured");
});

test("workstream READMEs without a title get a readable spine label", async ({ page }) => {
  await open(page);
  const nav = page.getByRole("navigation", { name: "Vault" });
  await expect(nav.getByRole("button", { name: "README", exact: true })).toHaveCount(0);
  await expect(nav.getByRole("button", { name: "Assets", exact: true })).toBeVisible();
});
