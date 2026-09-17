# 工程文档

主计划位于 [DEVELOPMENT_PLAN.md](../DEVELOPMENT_PLAN.md)，需求和原型仍保留在根目录。

- `architecture/`：系统边界、部署拓扑、管线与状态机。
- `api/`：契约约定、错误码、鉴权、幂等与兼容性说明；机器可读契约放 packages/contracts。
- `data/`：ER 图、数据字典、证据坐标、版本、用途及留存规则。
- `testing/`：回归基线、验收场景、脱敏评测汇总与阶段验收记录。
- `operations/`：启动、部署、监控、备份恢复、回滚及事件处置手册。
- `decisions/`：ADR，记录背景、选项、决定、影响、负责人及日期；按 `0001-topic.md` 编号。

S0 已补齐 architecture/data-model.md、api/contract.md、decisions/0001～0004、operations/development.md，以及 testing 下的回归与验收记录。其他目录按后续阶段补齐；机构授权决策仍待确认。

S2 的实现与验证见 [验收记录](testing/s2-report.md)、[运行手册](operations/s2.md)；真实服务和临床样本验收与本地合成验证分别记录。
