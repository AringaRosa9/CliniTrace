import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests/e2e",
  testMatch: "documents.spec.ts",
  outputDir: "test-results/s1",
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 20_000 },
  use: { baseURL: "http://127.0.0.1:13000", trace: "retain-on-failure" },
  webServer: [
    {
      command: "bash scripts/dev/s1-test-api.sh",
      url: "http://127.0.0.1:18000/api/v1/ready",
      timeout: 60_000,
      reuseExistingServer: false,
    },
    {
      command: "pnpm --filter frontend start",
      url: "http://127.0.0.1:13000",
      reuseExistingServer: false,
    },
  ],
});
