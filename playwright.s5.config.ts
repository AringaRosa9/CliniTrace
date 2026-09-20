import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests/e2e",
  testMatch: "release.spec.ts",
  outputDir: "test-results/s5/browser",
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 30_000 },
  use: { baseURL: "http://127.0.0.1:13000", trace: "retain-on-failure" },
  webServer: [
    {
      command:
        "EXTRACTION_PROVIDER=synthetic ALLOW_SYNTHETIC_MOCK=true TERMINOLOGY_VERSION=synthetic-terms-1.0.0 bash scripts/dev/s1-test-api.sh",
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
