import type { components } from "@/lib/api/schema";
export type Models = components["schemas"];
export type Review = Models["ReviewWorkspace"];
export type Fact = Models["FactRevision"];
export type Evidence = Models["Evidence"];
export const labels: Record<string, string> = {
  diagnoses: "诊断",
  history: "病史",
  duration: "病程",
  "medications.name": "药物",
  "medications.dose": "剂量",
  "medications.frequency": "频次",
  "observations.name": "检验项目",
  "observations.result": "检验结果",
  "observations.method": "方法",
  "observations.specimen": "标本",
};
export const missing: Record<string, string> = {
  missing: "应有而缺失",
  not_mentioned: "未提及",
  explicitly_unknown: "明确未知",
  not_applicable: "不适用",
  unreadable: "无法辨认",
};
export const states: Record<string, string> = {
  pending_review: "待审核",
  approved: "已审核",
  returned: "已退回",
  queued: "排队中",
  running: "生成中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};
export function factValue(f: Fact) {
  return `${f.value.comparator && f.value.comparator !== "=" ? f.value.comparator : ""}${f.value.normalized ?? missing[f.value.missing_reason ?? "missing"]}${f.value.unit ? " " + f.value.unit : ""}`;
}
