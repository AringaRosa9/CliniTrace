import { execFileSync } from "node:child_process";
import { test, expect } from "@playwright/test";
test("模板发布、词库激活、样本标注与独立二审门禁", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/templates");
  await page.getByLabel("开发访问码").fill("synthetic-e2e-only");
  await page.getByRole("button", { name: "进入工作空间" }).click();
  await expect(
    page.getByRole("heading", { name: "抽取模板", exact: true }),
  ).toBeVisible();
  const key = Date.now();
  const version = `outpatient-2.${key}.0`;
  await page.getByLabel("模板名称").fill(`合成门诊 ${key}`);
  await page.getByRole("button", { name: "载入基础字段" }).click();
  await expect(page.getByLabel("Schema JSON")).toContainText("diagnoses");
  await page
    .getByLabel("指南与语义说明")
    .fill("依据原文标注；剂量未知时保留明确未知；否定不得肯定化。");
  await page.getByRole("button", { name: "创建草稿", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: `合成门诊 ${key} · 修订 1` }),
  ).toBeVisible();
  await page.getByRole("button", { name: "校验已保存草稿" }).click();
  await expect(
    page.getByText("已保存草稿通过 Schema 与兼容性校验。", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("新版本号").fill(version);
  await page.getByRole("button", { name: "发布不可变版本" }).click();
  await expect(page.getByText(version, { exact: true })).toBeVisible();
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.screenshot({
      path: `docs/testing/baseline/s4-templates-${width}.png`,
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  const me = await (await page.request.get("/api/v1/me")).json();
  const project = me.projects[0].id,
    base = `/api/v1/projects/${project}`;
  const headers = {
    "X-CSRF-Token": me.csrf_token,
    Origin: "http://127.0.0.1:13000",
  };
  const templates = await (
    await page.request.get(base + "/template-versions")
  ).json();
  const template = templates.find(
    (t: { version: string }) => t.version === version,
  );
  await page.getByRole("link", { name: "术语管理", exact: true }).click();
  await page.getByText("导入词库新版本", { exact: true }).click();
  const termVersion = `browser-terms-${key}`;
  await page.getByLabel("版本标识").fill(termVersion);
  await page.getByLabel("编码体系").fill("synthetic-browser");
  await page
    .getByLabel("授权记录引用", { exact: true })
    .fill("synthetic-only-browser");
  await page.getByLabel("合成开发词库").check();
  await page.getByLabel("术语 JSON 数组").fill(
    JSON.stringify([
      {
        code: "SYN-T2",
        display: "2型糖尿病",
        aliases: ["2型糖尿病"],
        context: "合成病例诊断，非正式临床编码",
      },
    ]),
  );
  await page.getByRole("button", { name: "导入版本", exact: true }).click();
  await expect(
    page.getByRole("button", { name: new RegExp(termVersion) }),
  ).toBeVisible();
  await page.getByRole("button", { name: "激活此版本", exact: true }).click();
  await expect(
    page.getByRole("button", { name: new RegExp(`${termVersion}.*已激活`) }),
  ).toBeVisible();
  await page.getByLabel("术语、别名或编码").fill("2型糖尿病");
  await expect(
    page.getByRole("cell", { name: "SYN-T2", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "docs/testing/baseline/s4-terminology-1440.png",
    fullPage: true,
  });
  const patient = await (
    await page.request.post(base + "/patients", {
      headers,
      data: {
        patient_key: `s4-${key}`,
        source: "synthetic",
        source_key: `s4-${key}`,
      },
    })
  ).json();
  const encounter = await (
    await page.request.post(base + "/encounters", {
      headers,
      data: {
        patient_id: patient.id,
        kind: "outpatient",
        source: "synthetic",
        source_key: `s4-${key}`,
      },
    })
  ).json();
  const upload = await page.request.post(base + "/documents", {
    headers: { ...headers, "Idempotency-Key": `s4-${key}` },
    multipart: {
      patient_id: patient.id,
      encounter_id: encounter.id,
      document_type: "outpatient",
      source: "synthetic",
      authorization_reference: "synthetic-browser",
      file: {
        name: `s4-${key}.txt`,
        mimeType: "text/plain",
        buffer: Buffer.from(
          `SYNTHETIC-S2\ndiagnoses:2型糖尿病\nhistory:否认冠心病\nmedication:二甲双胍|剂量不详\n${key}`,
        ),
      },
    },
  });
  expect(upload.status()).toBe(202);
  const uploaded = await upload.json();
  await expect
    .poll(
      async () =>
        (await (await page.request.get(uploaded.status_url)).json()).status,
    )
    .toBe("succeeded");
  const doc = await (
    await page.request.get(base + "/documents/" + uploaded.document_id)
  ).json();
  const extraction = await page.request.post(
    base + `/documents/${doc.id}/extractions`,
    {
      headers: { ...headers, "Idempotency-Key": `s4-extract-${key}` },
      data: { template_version: version, parse_artifact_id: doc.artifact_id },
    },
  );
  expect(extraction.status()).toBe(202);
  const accepted = await extraction.json();
  await expect
    .poll(
      async () =>
        (await (await page.request.get(accepted.status_url)).json()).status,
    )
    .toBe("succeeded");
  const output = await (
    await page.request.get(base + `/extractions/${accepted.run_id}`)
  ).json();
  expect(output.configuration.template_digest).toBe(template.digest);
  await page.getByRole("link", { name: "质量评测", exact: true }).click();
  const existingReports = await (
    await page.request.get(base + "/quality/evaluations")
  ).json();
  if (!existingReports.length)
    await expect(
      page.getByRole("heading", { name: "尚无评测结果" }),
    ).toBeVisible();
  await page.screenshot({
    path: "docs/testing/baseline/s4-quality-empty-1440.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "金标准与裁决" }).click();
  await page.getByText("登记获准样本", { exact: true }).click();
  await page.getByLabel("抽取运行 ID", { exact: true }).fill(accepted.run_id);
  await page
    .getByLabel("受控患者组标识（跨来源同人使用相同标识）")
    .fill(`s4-${key}`);
  await page.getByLabel("样本来源", { exact: true }).fill("浏览器合成工程样本");
  await page
    .getByLabel("授权记录引用", { exact: true })
    .fill("synthetic-browser");
  await page.getByLabel("难例标签（逗号分隔）").fill("否定,未知剂量");
  await page.getByLabel("标注模板与指南版本").selectOption(template.id);
  await page.getByLabel("合成样本（仅工程验证）").check();
  await page.getByRole("button", { name: "登记样本", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "提交独立标注" }),
  ).toBeVisible();
  await expect(
    page.getByText("原文全文与证据坐标", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("独立标注结果 JSON").fill(
    JSON.stringify({
      facts: output.facts,
      relations: output.relations,
      codings: [],
    }),
  );
  await page.getByLabel("依据或分歧处理理由").fill("合成测试逐项核对");
  await page.getByRole("button", { name: "提交独立标注" }).click();
  await expect(
    page.getByRole("button", { name: "提交独立二审" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "提交独立二审" }).click();
  await expect(
    page.getByText("没有此项操作权限。", { exact: true }),
  ).toBeVisible();
  for (const width of [390, 768, 1024, 1440, 1920]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.screenshot({
      path: `docs/testing/baseline/s4-quality-${width}.png`,
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  }
  const samples = await (
    await page.request.get(base + "/quality/samples")
  ).json();
  const sample = samples.find(
    (s: { run_id: string }) => s.run_id === accepted.run_id,
  );
  execFileSync(
    "uv",
    [
      "run",
      "--project",
      "backend",
      "python",
      "tests/fixtures/synthetic/s4_review.py",
      project,
      sample.id,
    ],
    { env: { ...process.env, UV_CACHE_DIR: "/tmp/bljgh-uv-cache" } },
  );
  const goldResponse = await page.request.post(base + "/quality/datasets", {
    headers,
    data: { name: `合成冻结集 ${key}`, sample_ids: [sample.id] },
  });
  expect(goldResponse.status()).toBe(201);
  const gold = await goldResponse.json();
  const reportIds = [];
  for (const name of ["基线", "重放"]) {
    const response = await page.request.post(base + "/quality/evaluations", {
      headers,
      data: {
        name: `${name} ${key}`,
        dataset_id: gold.id,
        predictions: { [sample.id]: accepted.run_id },
      },
    });
    expect(response.status()).toBe(201);
    reportIds.push((await response.json()).id);
  }
  await page.reload();
  await page.getByLabel("当前评测运行").selectOption(reportIds[1]);
  await page
    .getByLabel("对比基线（同一冻结数据集）")
    .selectOption(reportIds[0]);
  await expect(
    page.getByRole("cell", { name: "否定变肯定", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "100.00%", exact: true }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "查看错误样本与重放输入" }).click();
  await expect(
    page.getByText("获准错误样本及预测快照", { exact: true }),
  ).toBeVisible();
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.screenshot({
      path: `docs/testing/baseline/s4-dashboard-${width}.png`,
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  }
  expect(errors).toEqual([]);
});

test("没有评测记录时显示可操作空态", async ({ page }) => {
  // Deterministic empty-state response; the preceding workflow uses the real database.
  await page.route("**/quality/evaluations", (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route("**/quality/datasets", (route) =>
    route.fulfill({ json: [] }),
  );
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/quality");
  await page.getByLabel("开发访问码").fill("synthetic-e2e-only");
  await page.getByRole("button", { name: "进入工作空间" }).click();
  await expect(
    page.getByRole("heading", { name: "尚无评测结果" }),
  ).toBeVisible();
  await expect(
    page.getByText("暂无实测数据；此处不会显示演示指标。", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "docs/testing/baseline/s4-quality-empty-1440.png",
    fullPage: true,
  });
});
