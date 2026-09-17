import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests/e2e",
  testMatch: "application.spec.ts",
  outputDir: "test-results/application",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:13000" },
  webServer: [
    {
      command:
        "uv run --project backend uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 18000",
      url: "http://127.0.0.1:18000/api/v1/health",
      reuseExistingServer: false,
    },
    {
      command: "pnpm --filter frontend start",
      url: "http://127.0.0.1:13000",
      reuseExistingServer: false,
    },
  ],
});
