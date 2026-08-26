import { describe, expect, it } from "vitest";

import { toAlertDetail, toAlertListItem, toAlertSummary } from "./alertView";
import { apiAlert, apiOverview } from "../test-fixtures/alerts";

describe("告警中文视图", () => {
  it("转换列表和统计中的业务含义", () => {
    expect(toAlertListItem(apiAlert())).toMatchObject({ state: "告警中", severity: "重要", sourceName: "生产 Prometheus", linked: true });
    expect(toAlertSummary({ window: "24h", active: 3, severe_active: 2, resolved: 4, unlinked_active: 1, by_source: [], calculated_at: "2026-08-26T08:00:00Z" })).toMatchObject({ active: 3, severeActive: 2, unlinkedActive: 1 });
  });

  it("将告警详情转换为检测结果和中文处理步骤", () => {
    const view = toAlertDetail(apiOverview());
    expect(view.resultText).toContain("18.4%");
    expect(view.steps.map((step) => step.title)).toEqual(["告警已认证接入", "重复信号已归并", "事故关联已完成"]);
    expect(view.facts).toEqual([{ name: "threshold", value: "10%" }, { name: "value", value: "18.4%" }]);
  });
});
