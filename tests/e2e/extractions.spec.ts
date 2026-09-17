import { test, expect } from "@playwright/test";
test("真实队列抽取、证据定位、候选版本切换和刷新", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/documents");
  await page.getByLabel("开发访问码").fill("synthetic-e2e-only");
  await page.getByRole("button", { name: "进入工作空间" }).click();
  await expect(
    page.getByRole("heading", { name: "文档任务", exact: true }),
  ).toBeVisible();
  const me = await (await page.request.get("/api/v1/me")).json();
  const project = me.projects[0].id;
  const base = `/api/v1/projects/${project}`;
  const headers = {
    "X-CSRF-Token": me.csrf_token,
    Origin: "http://127.0.0.1:13000",
  };
  const key = `s2-browser-${Date.now()}`;
  const patient = await (
    await page.request.post(base + "/patients", {
      headers,
      data: { patient_key: key, source: "synthetic", source_key: key },
    })
  ).json();
  const encounter = await (
    await page.request.post(base + "/encounters", {
      headers,
      data: {
        patient_id: patient.id,
        kind: "outpatient",
        source: "synthetic",
        source_key: key,
      },
    })
  ).json();
  const uploaded = await page.request.post(base + "/documents", {
    headers: { ...headers, "Idempotency-Key": key },
    multipart: {
      patient_id: patient.id,
      encounter_id: encounter.id,
      document_type: "outpatient",
      source: "synthetic",
      authorization_reference: "synthetic-only-v1",
      file: {
        name: key + ".txt",
        mimeType: "text/plain",
        buffer: Buffer.from(
          `SYNTHETIC-S2 😀\ndiagnoses:2型糖尿病\nhistory:否认冠心病\nduration:5年\nmedication:二甲双胍|剂量不详\n${key}`,
        ),
      },
    },
  });
  expect(uploaded.status()).toBe(202);
  await page.getByLabel("搜索文书").fill(key);
  await page.getByRole("button", { name: key + ".txt", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "开始抽取", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "开始抽取", exact: true }).click();
  await expect(
    page
      .getByRole("table")
      .filter({ has: page.getByText("结构化事实", { exact: false }) }),
  ).toBeVisible();
  await expect(page.locator(".extraction-facts")).toContainText("明确未知");
  await page
    .getByRole("button", { name: "定位证据 1 · 第 1 页" })
    .first()
    .click();
  await expect(page.locator(".source-text mark")).toHaveText("2型糖尿病");
  await page.getByRole("button", { name: "创建新抽取版本" }).click();
  await expect(
    page.getByRole("button", { name: "采用此版本并重新待审" }),
  ).toBeVisible();
  await page.getByLabel("采用新版本的理由").fill("合成回归核对");
  await page.getByRole("button", { name: "采用此版本并重新待审" }).click();
  await expect(page.getByText(/已完成 · 当前审核范围/)).toBeVisible();
  await page.reload();
  await page.getByLabel("搜索文书").fill(key);
  await page.getByRole("button", { name: key + ".txt", exact: true }).click();
  await expect(page.locator(".extraction-facts")).toContainText("二甲双胍");
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `docs/testing/baseline/s2-extraction-${width}.png`,
      fullPage: true,
    });
  }
  expect(errors).toEqual([]);
});
