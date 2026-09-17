import { test, expect, type Page } from "@playwright/test";
async function login(page: Page) {
  await page.goto("/documents");
  await page.getByLabel("开发访问码").fill("synthetic-e2e-only");
  await page.getByRole("button", { name: "进入工作空间" }).click();
  await expect(
    page.getByRole("heading", { name: "文档任务", exact: true }),
  ).toBeVisible();
}
for (const width of [390, 768, 1024, 1440, 1920]) {
  test(`文档工作空间布局与登录 ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await login(page);
    await expect(page.getByLabel("当前项目")).toContainText("合成文书验证项目");
    await expect(
      page.getByRole("button", { name: "+ 导入文书" }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await expect(page.getByText("正在读取已保存文书…")).toHaveCount(0);
    await page.screenshot({
      path: `docs/testing/baseline/s1-documents-${width}.png`,
      fullPage: true,
    });
    await page.getByRole("button", { name: "退出", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "登录文档工作空间" }),
    ).toBeVisible();
  });
}
test("建档、真实上传、队列解析、预览、刷新、更正、断网和会话清理", async ({
  page,
  context,
}) => {
  const failures: string[] = [];
  page.on("pageerror", (err) => failures.push(err.message));
  await login(page);
  await page.getByRole("button", { name: "+ 导入文书" }).click();
  await page.getByRole("button", { name: "新建患者", exact: true }).click();
  const key = `SYNTHETIC-${Date.now()}`;
  await page.getByLabel("项目内患者编号").fill(key);
  await page.getByLabel("患者来源系统").fill("synthetic-e2e");
  await page.getByLabel("患者来源标识").fill(key);
  await page.getByRole("button", { name: "保存患者", exact: true }).click();
  await page.getByRole("button", { name: "新建就诊", exact: true }).click();
  await page.getByLabel("就诊日期（可选）").fill("2026-09-17");
  await page.getByLabel("科室（可选）").fill("合成内分泌科");
  await page.getByLabel("就诊来源系统").fill("synthetic-e2e");
  await page.getByLabel("就诊来源标识").fill(key);
  await page.getByRole("button", { name: "保存就诊", exact: true }).click();
  await page.getByLabel("文件来源", { exact: true }).fill("合成测试样本");
  await page.getByLabel("授权记录引用").fill("synthetic-only-v1");
  const filename = `${key}.txt`;
  await page.getByLabel("选择原件").setInputFiles({
    name: filename,
    mimeType: "text/plain",
    buffer: Buffer.from(
      `合成门诊记录 😀\n患者编号 ${key}\n否认冠心病史。药物剂量不详。`,
    ),
  });
  await page.getByRole("button", { name: "保存并开始解析" }).click();
  await expect(page.getByRole("region", { name: "文书详情" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "原文预览", exact: true }),
  ).toBeVisible({ timeout: 45_000 });
  await expect(page.locator(".source-text")).toContainText("药物剂量不详");
  await page.getByLabel("搜索文书").fill(filename);
  await page.reload();
  await page.getByLabel("搜索文书").fill(filename);
  await page.getByRole("button", { name: filename, exact: true }).click();
  await expect(page.locator(".source-text")).toContainText(key);
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载原件", exact: true }).click();
  expect((await download).suggestedFilename()).toBe("document");
  await page.getByRole("button", { name: "更正关联", exact: true }).click();
  await page.getByLabel("查找患者编号").fill(key);
  await page
    .getByRole("combobox", { name: "选择患者", exact: true })
    .selectOption({ label: key });
  await expect(
    page
      .getByRole("combobox", { name: "选择就诊", exact: true })
      .locator("option"),
  ).toHaveCount(2);
  await page
    .getByRole("combobox", { name: "选择就诊", exact: true })
    .selectOption({ index: 1 });
  await page.getByLabel("更正理由").fill("合成测试：复核归属并记录修订");
  await page.getByRole("button", { name: "保存关联更正", exact: true }).click();
  await expect(page.locator(".document-meta")).toContainText("2");
  await context.setOffline(true);
  await expect(
    page.getByText(
      "网络已断开。已保存的记录仍在服务端，连接恢复后将自动刷新。",
    ),
  ).toBeVisible();
  await page.getByRole("button", { name: "刷新列表", exact: true }).click();
  // Reconnect and confirm the authoritative server copy is still available.
  await context.setOffline(false);
  await page.getByRole("button", { name: "刷新列表", exact: true }).click();
  await expect(page.locator(".source-text")).toContainText(key);
  await page.screenshot({
    path: "docs/testing/baseline/s1-parsed-document.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "退出", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "登录文档工作空间" }),
  ).toBeVisible();
  await expect(page.locator(".source-text")).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
  expect(failures).toEqual([]);
});
