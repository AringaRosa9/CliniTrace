import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
test("两文书工作台、审核、下载和修改失效", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/reviews");
  await page.getByLabel("开发访问码").fill("synthetic-e2e-only");
  await page.getByRole("button", { name: "进入工作空间" }).click();
  await expect(
    page.getByRole("heading", { name: "审核队列", exact: true }),
  ).toBeVisible();
  const me = await (await page.request.get("/api/v1/me")).json();
  const project = me.projects[0].id;
  const base = `/api/v1/projects/${project}`;
  const headers = {
    "X-CSRF-Token": me.csrf_token,
    Origin: "http://127.0.0.1:13000",
  };
  const key = `s3-browser-${Date.now()}`;
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
  for (const [kind, body] of [
    [
      "outpatient",
      "diagnoses:2型糖尿病\nhistory:否认冠心病\nduration:5年\nmedication:二甲双胍|剂量不详\n",
    ],
    ["laboratory", "observation:FPG|<7.00|mmol/L\n"],
  ]) {
    const uploaded = await page.request.post(base + "/documents", {
      headers: { ...headers, "Idempotency-Key": `${key}-${kind}` },
      multipart: {
        patient_id: patient.id,
        encounter_id: encounter.id,
        document_type: kind,
        source: "synthetic",
        authorization_reference: "synthetic-only-v1",
        file: {
          name: `${key}-${kind}.txt`,
          mimeType: "text/plain",
          buffer: Buffer.from(`SYNTHETIC-S2 😀\n${body}${key}`),
        },
      },
    });
    expect(uploaded.status()).toBe(202);
    const accepted = await uploaded.json();
    await expect
      .poll(
        async () =>
          (
            await (
              await page.request.get(
                base + "/documents/" + accepted.document_id,
              )
            ).json()
          ).processing_status,
      )
      .toBe("parsed");
    const doc = await (
      await page.request.get(base + "/documents/" + accepted.document_id)
    ).json();
    const extract = await page.request.post(
      base + "/documents/" + doc.id + "/extractions",
      {
        headers: { ...headers, "Idempotency-Key": `${key}-${kind}-extract` },
        data: {
          template_version: kind + "-1.0.0",
          parse_artifact_id: doc.artifact_id,
        },
      },
    );
    expect(extract.status()).toBe(202);
    const run = await extract.json();
    await expect
      .poll(
        async () =>
          (await (await page.request.get(run.status_url)).json()).status,
      )
      .toBe("succeeded");
  }
  const scope = await (
    await page.request.get(base + "/encounters/" + encounter.id + "/review-set")
  ).json();
  await page.goto(`/projects/${project}/reviews/${scope.id}`);
  await expect(
    page.getByRole("heading", { name: "审核工作台", exact: true }),
  ).toBeVisible();
  expect(
    (
      await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
        .analyze()
    ).violations,
  ).toEqual([]);
  await expect(
    page.getByRole("heading", { name: "审核工作台", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".case-context")).toContainText("2 份关联文书");
  await page.getByRole("button", { name: "证据 1 · 第 1 页" }).first().click();
  await expect(page.locator(".source-block mark")).toBeVisible();
  await page.locator(".source-block").first().click();
  await expect(
    page.getByRole("group", { name: "重叠证据关联字段" }),
  ).toBeVisible();
  await page
    .getByRole("group", { name: "重叠证据关联字段" })
    .getByRole("button")
    .first()
    .click();
  await page.getByLabel("原文缩放").selectOption("150");
  await expect(page.locator(".source-block mark")).toBeVisible();
  await page.getByRole("button", { name: "病程时间线", exact: true }).click();
  await expect(page.locator(".timeline")).toContainText("5年");
  await page.getByRole("button", { name: "JSON", exact: true }).click();
  await expect(page.locator(".review-json")).toContainText(
    "explicitly_unknown",
  );
  await page.getByRole("button", { name: "抽取字段", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "完成审核并冻结快照" }),
  ).toBeDisabled();
  for (const article of await page.locator(".review-issue").all()) {
    const accept = article.getByRole("button", { name: "接受未知并保留原文" });
    if (await accept.count()) {
      await article
        .getByLabel("处置理由")
        .fill("已核对原文，保留未知及待映射状态");
      await accept.click();
      await expect(article).toContainText("已接受未知");
    }
  }
  for (
    let remaining = await page
      .getByRole("button", { name: "确认此项", exact: true })
      .count();
    remaining > 0;
    remaining--
  ) {
    const button = page
      .getByRole("button", { name: "确认此项", exact: true })
      .first();
    await expect(button).toBeEnabled();
    await button.click();
    await expect(
      page.getByRole("button", { name: "确认此项", exact: true }),
    ).toHaveCount(remaining - 1);
  }
  await page
    .getByLabel(
      "我已逐项核对原文、事实、未知语义及问题处置，确认完成本次审核。",
    )
    .check();
  await page.getByRole("button", { name: "完成审核并冻结快照" }).click();
  await expect(page.locator(".case-context")).toContainText("已审核");
  for (const width of [390, 768, 1024, 1440, 1920]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `docs/testing/baseline/s3-review-${width}.png`,
      fullPage: true,
    });
  }
  await page.getByRole("button", { name: "修订历史", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("不可变审核快照");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "修订历史", exact: true }),
  ).toBeFocused();
  await page.goto(`/projects/${project}/datasets`);
  await page.getByLabel("患者键", { exact: true }).fill(key);
  await expect(page.locator(".dataset-counts")).toContainText("已审核 1 次");
  await page.getByLabel(`选择患者 ${key}`).check();
  await page.getByLabel("数据用途").fill(`合成闭环浏览器验收 ${key}`);
  await page.getByRole("button", { name: "生成导出文件" }).click();
  const latest = page
    .locator(".export-history article")
    .filter({ hasText: `合成闭环浏览器验收 ${key}` });
  await expect(latest).toContainText("已完成");
  const downloaded = page.waitForEvent("download");
  await latest.getByRole("button", { name: "下载 JSON" }).click();
  expect((await downloaded).suggestedFilename()).toMatch(/\.json$/);
  await page.goto(`/projects/${project}/reviews/${scope.id}`);
  await expect(
    page.getByRole("heading", { name: "审核工作台", exact: true }),
  ).toBeVisible();
  expect(
    (
      await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
        .analyze()
    ).violations,
  ).toEqual([]);
  await page.getByRole("button", { name: "编辑 / 排除" }).first().click();
  await page.getByLabel("修改理由").fill("审核后再次核对原文并记录修订");
  await page.getByRole("button", { name: "保存修订", exact: true }).click();
  await expect(page.locator(".case-context")).toContainText("待审核");
  await page.reload();
  await expect(page.locator(".fact-table")).toContainText(
    "审核后再次核对原文并记录修订",
  );
  // Preserve a local edit when another actor changes the same scope.
  await page.getByRole("button", { name: "编辑 / 排除" }).first().click();
  await page.getByLabel("修改理由").fill("尚未提交的本地理由");
  const current = await (
    await page.request.get(base + `/review-sets/${scope.id}`)
  ).json();
  const fact = current.facts[0];
  expect(
    (
      await page.request.patch(base + `/facts/${fact.fact_id}`, {
        headers,
        data: {
          expected_scope_revision: current.scope_revision,
          expected_revision: fact.revision,
          reason: "另一个审核者的修订",
          value: fact.value,
          evidence: fact.evidence,
          event_time: fact.event_time,
        },
      })
    ).status(),
  ).toBe(200);
  await page.getByRole("button", { name: "保存修订", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "版本已变化",
  );
  await expect(page.getByLabel("修改理由")).toHaveValue("尚未提交的本地理由");
  await page.getByRole("button", { name: "刷新审核范围" }).click();
  await expect(page.locator(".fact-editor")).toContainText("范围已更新");
  await page.getByRole("button", { name: "关闭编辑" }).click();
  await page.getByRole("button", { name: "放弃未保存修改" }).click();
  expect(errors).toEqual([]);
});
