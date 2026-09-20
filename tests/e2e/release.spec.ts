import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";

const tags = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"];
test("S5 登录、六页面、键盘和多视口无障碍检查", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/documents");
  await expect(page.getByLabel("开发访问码")).toBeVisible();
  const login = await new AxeBuilder({ page }).withTags(tags).analyze();
  expect(login.violations).toEqual([]);
  await page.getByLabel("开发访问码").fill("synthetic-e2e-only");
  await page.getByRole("button", { name: "进入工作空间" }).click();
  await expect(page.getByRole("button", { name: "退出" })).toBeVisible();
  const summary = [];
  for (const width of [390, 768, 1024, 1440, 1920]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const route of [
      "documents",
      "reviews",
      "templates",
      "terminology",
      "datasets",
      "quality",
    ]) {
      await page.goto(`/${route}`);
      await expect(page.locator("main h1")).toBeVisible();
      await expect(page.getByText("正在载入工作空间…")).toHaveCount(0);
      const result = await new AxeBuilder({ page }).withTags(tags).analyze();
      summary.push({ route, width, violations: result.violations });
      expect.soft(result.violations, `${route} at ${width}`).toEqual([]);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
    }
  }
  // A keyboard user can skip repeated navigation and reach the workspace.
  await page.goto("/documents");
  await expect(
    page.getByRole("heading", { name: "文档任务", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("link", { name: "跳转到主要内容" }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("main")).toBeFocused();
  // 200% CSS zoom checks reflow, without claiming a manual screen-reader audit.
  await page.evaluate(() => {
    document.documentElement.style.zoom = "2";
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  mkdirSync("test-results/s5", { recursive: true });
  writeFileSync(
    "test-results/s5/accessibility.json",
    JSON.stringify(summary, null, 2),
  );
  await page.screenshot({
    path: "test-results/s5/accessibility-desktop.png",
    fullPage: true,
  });
});

test("S5 安全响应与授权 API 合成负载", async ({ page }) => {
  await page.goto("/documents");
  await page.getByLabel("开发访问码").fill("synthetic-e2e-only");
  await page.getByRole("button", { name: "进入工作空间" }).click();
  await expect(page.getByRole("button", { name: "退出" })).toBeVisible();
  const response = await page.request.get("/documents");
  expect(response.headers()["x-content-type-options"]).toBe("nosniff");
  expect(response.headers()["content-security-policy"]).toContain(
    "object-src 'none'",
  );
  expect(response.headers()["content-security-policy"]).not.toContain(
    "unsafe-eval",
  );
  const me = await (await page.request.get("/api/v1/me")).json();
  const cookie = (await page.context().cookies()).find(
    (c) => c.name === "bljgh_session",
  );
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.sameSite).toBe("Strict");
  expect((await page.request.get("/internal/metrics")).status()).toBe(404);
  const denied = await page.request.post(
    `/api/v1/projects/${me.projects[0].id}/patients`,
    {
      data: {
        patient_key: "blocked",
        source: "synthetic",
        source_key: "blocked",
      },
    },
  );
  expect(denied.status()).toBe(403);
  execFileSync(
    "backend/.venv/bin/python",
    [
      "tests/performance/api_load.py",
      "--project",
      me.projects[0].id,
      "--requests",
      "200",
      "--concurrency",
      "8",
      "--p95-ms",
      "1000",
    ],
    {
      env: { ...process.env, LOAD_SESSION: cookie!.value },
      stdio: "pipe",
      timeout: 90_000,
    },
  );
});
