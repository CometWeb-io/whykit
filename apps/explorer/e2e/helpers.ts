import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

/** Open a route and wait until React has rendered it and attached listeners. */
export async function open(page: Page, hash = "", base?: string): Promise<void> {
  await page.goto(`${base ?? ""}${base ? "" : "./"}${hash}`);
  await expect(page.locator("main h1")).toBeVisible();
  await expect(page.locator("header")).toBeVisible();
}

/** Serious and critical axe violations, formatted for a readable failure. */
export async function seriousViolations(page: Page): Promise<string[]> {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"])
    .analyze();
  return result.violations
    .filter(v => v.impact === "serious" || v.impact === "critical")
    .map(v => `${v.id} (${v.impact ?? "?"}): ${v.nodes.map(n => n.target.join(" ")).join(", ")}`);
}

export const PAGES = [
  { hash: "", nav: "Home", h1: "WhyKit — Northline example vault" },
  { hash: "#view=decisions", nav: "Decisions", h1: "Decisions" },
  { hash: "#view=timeline", nav: "Timeline", h1: "Decision timeline" },
  { hash: "#view=evidence", nav: "Evidence", h1: "Evidence" },
  { hash: "#view=freshness", nav: "Freshness", h1: "Evidence freshness" },
  { hash: "#view=reviews", nav: "Reviews", h1: "Reviews" },
  { hash: "#view=graph", nav: "Graph", h1: "Knowledge graph" },
  { hash: "#view=health", nav: "Health", h1: "Health" },
  { hash: "#view=templates", nav: "Templates", h1: "Templates" },
  { hash: "#view=adopt", nav: "Adopt", h1: "Adopt WhyKit" },
] as const;
