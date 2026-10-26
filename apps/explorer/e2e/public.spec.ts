import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { gunzipSync } from "node:zlib";
import { expect, test } from "@playwright/test";

const BUILD = resolve(import.meta.dirname, ".build");

test("public builds omit private notes, rows and dependent titles from every asset", () => {
  for (const site of ["public", "public-single", "public-md", "public-md-single", "public-name", "public-name-single"]) {
    const root = join(BUILD, site);
    const files = readdirSync(root, { recursive: true, withFileTypes: true }).filter(file => file.isFile());
    let artifact = files.map(file => readFileSync(join(file.parentPath, file.name), "utf8")).join("\n");
    if (site.endsWith("-single")) {
      const blocks = [...artifact.matchAll(/<script type="application\/json" id="whykit-chunk-[^"]+">([^<]+)<\/script>/g)];
      expect(blocks).toHaveLength(3);
      artifact += blocks.map(match => gunzipSync(Buffer.from(match[1]!, "base64")).toString("utf8")).join("\n");
    }
    expect(artifact.includes("VISIBLE-PUBLIC-CANARY"), site + " retains public text").toBe(true);
    expect(artifact.includes("PUBLIC-ROW-CANARY"), site + " retains public evidence").toBe(true);
    for (const marker of ["PRIVATE-ROW-CANARY", "PRIVATE-TITLE-CANARY", "PRIVATE-BODY-CANARY",
      "DEPENDENT-TITLE-CANARY", "ROW-DEPENDENT-CANARY", "notes/private-source", "notes/private-reference", "E-001"]) {
      expect(artifact.includes(marker), site + " omits " + marker).toBe(false);
    }
  }
});

for (const site of ["public-single", "public-md-single", "public-name-single"]) {
  test(`${site} renders its retained note and public evidence`, async ({ page }) => {
    const url = pathToFileURL(join(BUILD, site, "index.html")).href;
    await page.goto(url + "#doc=notes%2Fpublic-safe");
    await expect(page.locator("main h1")).toContainText("Public export fixture");
    await expect(page.locator("main")).toContainText("VISIBLE-PUBLIC-CANARY");
    await page.goto(url + "#view=evidence");
    await expect(page.locator("main h1")).toHaveText("Evidence");
    await expect(page.locator("main")).toContainText("PUBLIC-ROW-CANARY");
  });
}
