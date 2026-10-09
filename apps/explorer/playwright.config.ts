import { defineConfig, devices } from "@playwright/test";
import { EMPTY_PORT, NORTHLINE_PORT, SYNTHETIC_PORT } from "./e2e/ports.ts";

// The suite runs against static production builds (see scripts/build-e2e.mjs),
// served by `vite preview`, using Playwright's own bundled Chromium only.

function preview(name: string, port: number) {
  return {
    command: `node node_modules/vite/bin/vite.js preview --outDir e2e/.build/${name} --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}/`,
    reuseExistingServer: false,
    timeout: 30_000,
  };
}

export default defineConfig({
  testDir: "e2e",
  outputDir: "test-results",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${NORTHLINE_PORT}/`,
    trace: "retain-on-failure",
    colorScheme: "dark",
    timezoneId: "UTC",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1360, height: 900 } }, testIgnore: /mobile\.spec\.ts/ },
    { name: "mobile", use: { ...devices["Pixel 7"] }, testMatch: /mobile\.spec\.ts/ },
  ],
  webServer: [preview("northline", NORTHLINE_PORT), preview("empty", EMPTY_PORT), preview("synthetic", SYNTHETIC_PORT), preview("claims", 4320), preview("claims-hidden", 4321)],
});
