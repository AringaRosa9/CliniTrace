import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests/e2e",
  testMatch: "prototype.spec.ts",
  fullyParallel: true,
  workers: 3,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: "test-results/baseline.json" }]],
  use: {
    baseURL: "http://127.0.0.1:8765",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "python3 -m http.server 8765 --bind 127.0.0.1",
    url: "http://127.0.0.1:8765/index.html",
    reuseExistingServer: !process.env.CI,
  },
});
