import { expect, test } from "@playwright/test";
import { open } from "./helpers.ts";

test("the search dialog is fully keyboard driven", async ({ page }) => {
  await open(page);
  const trigger = page.getByRole("button", { name: "Search vault" });
  await trigger.focus();
  await page.keyboard.press("ControlOrMeta+k");
  const dialog = page.getByRole("dialog", { name: "Search vault" });
  await expect(dialog).toBeVisible();
  const box = dialog.getByRole("combobox", { name: "Search vault" });
  await expect(box).toBeFocused();
  await expect(dialog.getByRole("status")).toContainText("Type to search");

  await page.keyboard.type("hubspot");
  const options = dialog.getByRole("option");
  await expect(options.first()).toBeVisible();
  const count = await options.count();
  expect(count).toBeGreaterThan(1);
  await expect(dialog.getByRole("status")).toHaveText(`${count} results`);
  await expect(box).toHaveAttribute("aria-activedescendant", "search-option-0");
  await expect(options.nth(0)).toHaveAttribute("aria-selected", "true");

  await page.keyboard.press("ArrowDown");
  await expect(box).toHaveAttribute("aria-activedescendant", "search-option-1");
  await expect(options.nth(1)).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("ArrowUp");
  // Wraps from the first row to the last.
  await expect(box).toHaveAttribute("aria-activedescendant", `search-option-${count - 1}`);

  // Focus stays trapped between the input and the close button.
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Close search" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(box).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "Close search" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(box).toBeFocused();

  await page.keyboard.press("ArrowDown");
  const title = (await options.nth(0).locator("strong").textContent()) ?? "";
  await page.keyboard.press("Enter");
  await expect(dialog).toBeHidden();
  await expect(page.locator("main h1")).toHaveText(title);
  await expect(page.locator("main")).toBeFocused();
});

test("typing a new query resets the highlighted result", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Search vault" }).click();
  const box = page.getByRole("combobox", { name: "Search vault" });
  await box.fill("decision");
  await expect(page.getByRole("option").nth(2)).toBeVisible();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await expect(box).toHaveAttribute("aria-activedescendant", "search-option-2");
  await page.keyboard.type(" log");
  await expect(box).toHaveAttribute("aria-activedescendant", "search-option-0");
});

test("Escape closes the dialog and returns focus to the opener", async ({ page }) => {
  await open(page);
  const trigger = page.getByRole("button", { name: "Search vault" });
  await trigger.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.locator("body")).toHaveCSS("overflow", "hidden");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(trigger).toBeFocused();
  await expect(page.locator("body")).not.toHaveCSS("overflow", "hidden");
});

test("a query with no hits says so and Enter does nothing", async ({ page }) => {
  await open(page, "#view=health");
  await page.keyboard.press("ControlOrMeta+k");
  await page.keyboard.type("zzzz-nothing-matches");
  await expect(page.getByRole("dialog").getByRole("status")).toHaveText("No matching notes.");
  await expect(page.getByRole("combobox")).not.toHaveAttribute("aria-activedescendant", /.+/);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.locator("main h1")).toHaveText("Health");
});

test("clicking the backdrop closes the dialog", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Search vault" }).click();
  await page.mouse.click(10, 10);
  await expect(page.getByRole("dialog")).toBeHidden();
});
