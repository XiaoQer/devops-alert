import { expect, it } from "vitest";

import { toEvidenceDetailView, toEvidenceItemView, toEvidenceRunView } from "./evidenceView";

it("把部分成功和无数据区分成中文状态", () => {
  expect(toEvidenceRunView({ state: "PARTIAL" }).stateLabel).toBe("部分证据缺失");
  expect(toEvidenceItemView({ state: "NO_DATA", source_type: "PROMETHEUS" }).stateLabel).toBe("未发现数据");
});

it("缺失和失败证据默认标记为需要关注", () => {
  expect(toEvidenceItemView({ state: "FAILED", source_type: "SKYWALKING" }).needsAttention).toBe(true);
  expect(toEvidenceItemView({ state: "SUCCEEDED", source_type: "PROMETHEUS" }).needsAttention).toBe(false);
});

it("只把最多五条有效监控事实集中到详情摘要", () => {
  const detail = toEvidenceDetailView({
    run: { state: "PARTIAL" },
    items: [
      { id: "log", state: "SUCCEEDED", evidence_type: "LOG_SAMPLES", source_type: "ELASTICSEARCH", display_name: "日志", interpretation: "已取得日志样本" },
      ...Array.from({ length: 6 }, (_, index) => ({
        id: `ok-${index}`,
        state: "SUCCEEDED",
        evidence_type: "METRIC_COMPARISON",
        source_type: "PROMETHEUS",
        display_name: `指标 ${index}`,
        interpretation: `事实 ${index}`,
      })),
      { id: "missing", state: "SKIPPED_DEPENDENCY", source_type: "SKYWALKING", interpretation: "未配置" },
    ],
  });

  expect(detail.keyFacts).toEqual([
    { id: "ok-0", title: "指标 0", text: "事实 0", sourceLabel: "Prometheus" },
    { id: "ok-1", title: "指标 1", text: "事实 1", sourceLabel: "Prometheus" },
    { id: "ok-2", title: "指标 2", text: "事实 2", sourceLabel: "Prometheus" },
    { id: "ok-3", title: "指标 3", text: "事实 3", sourceLabel: "Prometheus" },
    { id: "ok-4", title: "指标 4", text: "事实 4", sourceLabel: "Prometheus" },
  ]);
});
