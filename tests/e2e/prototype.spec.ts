import { test, expect, type Page } from "@playwright/test";
import { readFileSync, mkdirSync } from "node:fs";
const baseline = "docs/testing/baseline";
mkdirSync(baseline, { recursive: true });
test.beforeEach(async ({ page }) => {
  await page.goto("/index.html");
});
async function resolveIssues(page: Page) {
  for (const key of ["dose", "coding"]) {
    await page.locator(`[data-issue="${key}"]`).click();
    await page
      .locator("#resolutionNote")
      .fill("依据合成原文保留未知或待映射。");
    await page.locator("#saveResolution").click();
  }
}
async function approve(page: Page) {
  await resolveIssues(page);
  await page.locator("#reviewAck").check();
  await page.locator('[data-action="approve"]').click();
}
async function download(page: Page, format: "json" | "csv") {
  await page.locator('[data-action="export"]').click();
  await page.locator("#exportFormat").selectOption(format);
  const wait = page.waitForEvent("download");
  await page.locator("#downloadExport").click();
  const file = await wait;
  return readFileSync((await file.path())!, "utf8");
}
test("01 默认三栏及演示标记", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect(page.locator(".demo-badge")).toContainText("合成数据");
  await expect(page.locator(".review-grid > section")).toHaveCount(2);
  await expect(page.locator(".inspector")).toBeVisible();
});
test("02 检验字段双向定位", async ({ page }) => {
  await page.locator('[data-field="glucose"]').click();
  await expect(page.locator('[data-doc="lab"]')).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.locator('[data-evidence="glucose"]')).toHaveClass(
    /selected/,
  );
  await page.locator('[data-evidence="hba1c"]').click();
  await expect(page.locator('[data-field="hba1c"]')).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});
test("03 编辑原因、当前值与审计同步", async ({ page }) => {
  await page.locator('[data-field="glucose"]').click();
  await page.locator('[data-action="editSelected"]').click();
  await page.locator("#editValue").fill("8.3");
  await page.locator("#saveEdit").click();
  await expect(page.locator("#editError")).not.toBeEmpty();
  await page.locator("#editReason").fill("合成回归修改记录");
  await page.locator("#saveEdit").click();
  await expect(page.locator('[data-field="glucose"]')).toContainText("8.3");
  await page.locator('[data-action="history"]').click();
  await expect(page.locator("#drawerBody")).toContainText("8.2 → 8.3");
  await expect(page.locator("#drawerBody")).toContainText("合成回归修改记录");
});
test("04 未处置异常阻断，允许未知与待映射", async ({ page }) => {
  await page.locator("#reviewAck").check();
  await page.locator('[data-action="approve"]').click();
  await expect(page.locator("#toast")).toContainText("还有 2 项");
  await resolveIssues(page);
  await expect(page.locator(".issue.resolved")).toHaveCount(2);
  await expect(page.locator('[data-field="dose"]')).toContainText("未知");
});
test("05 最终确认、审核状态及数据集联动", async ({ page }) => {
  await resolveIssues(page);
  await page.locator('[data-action="approve"]').click();
  await expect(page.locator("#reviewAck")).toBeFocused();
  await page.locator("#reviewAck").check();
  await page.locator('[data-action="approve"]').click();
  await expect(page.locator('[data-action="approve"]')).toBeDisabled();
  await page.locator('[data-page="datasets"]').click();
  await expect(page.locator("#main")).toContainText("已审核");
  await expect(page.locator(".metric").nth(2).locator("strong")).toHaveText(
    "1",
  );
});
test("06 JSON CSV 当前值、证据、版本、草稿标记", async ({ page }) => {
  const draft = JSON.parse(await download(page, "json"));
  expect(draft.review_status).toBe("draft");
  expect(draft.facts[4].missing_reason).toBe("explicitly_unknown");
  expect(draft.facts[4].value).toBeNull();
  expect(draft.facts[2].assertion).toBe("negated");
  expect(draft.schema_version).toBeTruthy();
  expect(
    draft.facts.every((f: { evidence: { quote: string } }) => f.evidence.quote),
  ).toBeTruthy();
  await approve(page);
  const approved = JSON.parse(await download(page, "json"));
  expect(approved.review_status).toBe("approved");
  const csv = await download(page, "csv");
  expect(csv).toContain("approved");
  expect(csv).toContain("原文证据");
  expect(csv).toContain("模板版本");
});
test("07 搜索与本地文件预览", async ({ page }) => {
  await page.locator('[data-page="documents"]').click();
  await page.locator("#documentSearch").fill("检验");
  await expect(page.locator("#documentRows tr")).toHaveCount(1);
  await page.locator("#documentSearch").fill("");
  await page.locator('[data-action="upload"]').click();
  await page
    .locator("#fileInput")
    .setInputFiles("tests/fixtures/synthetic/outpatient.txt");
  await expect(page.locator("#uploadList")).toContainText("未进行抽取");
  await page.locator("#finishImport").click();
  await page.locator('[data-preview="0"]').click();
  await expect(page.locator("#importPreview")).toContainText("合成🧪");
  await expect(page.locator("#drawerBody")).toContainText("未生成结构化结果");
});
test("08 六页面、时间线、JSON、指南及模拟指标", async ({ page }) => {
  await page.locator('[data-tab="timeline"]').click();
  await expect(page.locator(".timeline")).toContainText("无法确定精确起病日期");
  await page.locator('[data-tab="json"]').click();
  await expect(page.locator(".json-view")).toContainText("explicitly_unknown");
  for (const p of [
    "documents",
    "review",
    "templates",
    "terminology",
    "datasets",
    "quality",
  ]) {
    await page.locator(`[data-page="${p}"]`).click();
    await expect(page.locator("h1")).toBeVisible();
  }
  await expect(page.locator("#main")).toContainText("模拟结果 · 非实测");
  await page.locator('[data-page="templates"]').click();
  await page.locator('[data-action="viewGuide"]').click();
  await expect(page.locator("#drawerBody")).toContainText(
    "尚未经过医学专家审定",
  );
});
test("09 键盘抽屉焦点锁定与恢复", async ({ page }) => {
  const help = page.locator("#helpButton");
  await help.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#closeDrawer")).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(page.locator("#drawerBody a").last()).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.locator("#closeDrawer")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(help).toBeFocused();
  expect(
    await help.evaluate((el) => getComputedStyle(el).outlineStyle),
  ).not.toBe("none");
});
for (const width of [390, 768, 1024, 1440, 1920])
  test(`基线截图 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await expect(page.locator(".document-scroll")).toBeVisible();
    await expect(page.locator(".fields-body")).toBeVisible();
    await expect(page.locator('[data-action="approve"]')).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `${baseline}/prototype-${width}.png`,
      fullPage: true,
      animations: "disabled",
    });
  });
