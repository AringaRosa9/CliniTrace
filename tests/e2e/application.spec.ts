import { test, expect } from "@playwright/test";
for (const width of [390, 768, 1440])
  test(`应用骨架与同源健康检查 ${width}`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toContainText(
      "有据可循",
    );
    await expect(page.getByText("API 进程可用")).toBeVisible();
    await page.getByRole("button", { name: "刷新状态" }).click();
    await expect(page.getByRole("button", { name: "刷新状态" })).toBeEnabled();
    const response = await page.request.get("/api/v1/health");
    expect(response.status()).toBe(200);
    expect((await response.json()).service).toBe("clinical-data-api");
    expect(response.headers()["cache-control"]).toContain("no-store");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    expect(errors).toEqual([]);
    await page.screenshot({
      path: `docs/testing/baseline/application-${width}.png`,
      fullPage: true,
    });
  });
