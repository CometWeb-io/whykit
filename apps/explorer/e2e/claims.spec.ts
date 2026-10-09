import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { open } from "./helpers.ts";
import { hrefFor } from "../src/lib/route.ts";
const CLAIMS = "http://127.0.0.1:4320/", HIDDEN = "http://127.0.0.1:4321/";
for (const id of ["00-context/claims/c-001-offline-reads", "06-decisions/d-001-evaluate-offline-reads"]) {
  test(`claim conflict is explicit on ${id}`, async ({ page }) => {
    await open(page, hrefFor("doc", id), CLAIMS);
    const panel = page.getByRole("region", { name: "Claim assessment" });
    await expect(panel.getByText("Disputed", { exact: true })).toBeVisible();
    await expect(panel.getByText("Supports", { exact: true })).toBeVisible();
    await expect(panel.getByText("Contradicts", { exact: true })).toBeVisible();
    await expect(panel.getByText("E-001", { exact: true })).toBeVisible();
    await expect(panel.getByText("E-002", { exact: true })).toBeVisible();
    await expect(panel.getByText(/Desktop v2.1 without synchronization/)).toBeVisible();
    await expect(panel.getByText(/2026-10-09/).first()).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}
test("hidden claim export exposes no title, snapshot hash or private receipt", async ({ page }) => {
  await open(page, hrefFor("doc", "00-context/claims/c-001-offline-reads"), HIDDEN);
  await expect(page.locator("main h1")).toHaveText("Document not found");
  const text = await page.locator("main").innerText();
  expect(text).not.toContain("Cached decisions are readable offline.");
  expect(text).not.toContain("claim-receipt/v1:");
  expect(text).not.toMatch(/[0-9a-f]{64}/);
});
