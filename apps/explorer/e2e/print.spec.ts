import { expect, test, type Locator } from "@playwright/test";
import { open } from "./helpers.ts";

// A decision record printed (or saved as PDF) from the browser: the record,
// its supersession chain and its registered evidence, dark on white, without
// the application chrome.

const DOC = "#doc=06-decisions%2Fd-011-direct-with-evidence";

/** Relative luminance of a computed CSS colour, 0 (black) to 1 (white). */
async function luminance(target: Locator, property: "color" | "background-color"): Promise<number> {
  const value = await target.evaluate((el, p) => getComputedStyle(el).getPropertyValue(p), property);
  const [r, g, b] = (value.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number).map(c => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * (r ?? 0) + 0.7152 * (g ?? 0) + 0.0722 * (b ?? 0);
}

test("a decision record prints without navigation, dark on white", async ({ page }) => {
  await open(page, DOC);
  await expect(page.locator("main .markdown p").first()).toBeVisible();
  await page.emulateMedia({ media: "print" });
  for (const chrome of ["header", "#sidebar", ".skip"]) await expect(page.locator(chrome)).toBeHidden();
  expect(await page.locator("main").evaluate(el => getComputedStyle(el).marginLeft)).toBe("0px");
  expect(await luminance(page.locator("body"), "background-color")).toBeGreaterThan(0.95);
  for (const text of ["main h1", "main .markdown p", ".chain-title", ".cards .card h3"]) {
    expect(await luminance(page.locator(text).first(), "color"), `${text} is not dark ink`).toBeLessThan(0.1);
  }
});

test("the chain and the evidence print in full and are not split across pages", async ({ page }) => {
  await open(page, DOC);
  await expect(page.locator("main .markdown p").first()).toBeVisible();
  await page.emulateMedia({ media: "print" });
  await expect(page.locator(".chain .chain-step")).toHaveCount(3);
  await expect(page.locator(".chain .chain-step").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Registered evidence" })).toBeVisible();
  for (const block of [".chain-card", ".cards .card", ".lineage"]) {
    expect(await page.locator(block).first().evaluate(el => getComputedStyle(el).breakInside), block).toBe("avoid");
  }
  // Wide tables and code blocks wrap on paper instead of being clipped by a scroll box.
  for (const block of [".table-wrap", ".markdown pre"]) {
    const el = page.locator(block).first();
    if (await el.count()) expect(await el.evaluate(e => getComputedStyle(e).overflow), block).toBe("visible");
  }
  const pdf = await page.pdf({ format: "A4", printBackground: true });
  expect(pdf.subarray(0, 5).toString()).toBe("%PDF-");
});

test("an open search dialog does not print over the record", async ({ page }) => {
  await open(page, `${DOC}&search=pricing`);
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.emulateMedia({ media: "print" });
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(page.locator("main h1")).toBeVisible();
});
