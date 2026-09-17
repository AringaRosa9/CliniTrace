# AI 抽取资产

状态：预留版本化资产目录，尚无模型接入。

- `prompts/`：系统任务、文书分类和字段抽取提示词；记录版本与变更原因。
- `schemas/`：后续抽取配置入口。S0 模型唯一源为 `backend/app/contracts/models.py`，门诊／检验 JSON Schema 自动发布到 `packages/contracts/json-schema/`，不在此重复手写。
- `rules/`：可审阅规则定义、严重度、未知处置约束及版本说明；执行逻辑放后端。

运行时适配器位于 `backend/app/integrations/`，质量评测位于 `evaluation/`，避免在此建立第二套服务。模型不得自由生成术语编码，真实病例与完整模型响应不得提交仓库。每次运行固定模板、提示词、规则与模型版本。

S2 运行资产：`prompts/extraction-1.0.0.txt`、`rules/extraction-1.0.0.json`、`schemas/synthetic-terms-1.0.0.json`。运行固定内容摘要，不能用同版本静默替换在途输入。合成词库只用于测试，不包含正式医学编码。
