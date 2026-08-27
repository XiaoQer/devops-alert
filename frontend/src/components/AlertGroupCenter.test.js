import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import { fetchAlertGroupMembers, fetchAlertGroupOverview, fetchAlertGroups, fetchAlertGroupSummary } from "../api/alertGroups";
import AlertGroupCenter from "./AlertGroupCenter.vue";

vi.mock("../api/alertGroups", () => ({
  fetchAlertGroupMembers: vi.fn(), fetchAlertGroupOverview: vi.fn(),
  fetchAlertGroups: vi.fn(), fetchAlertGroupSummary: vi.fn(),
}));

const group = {
  id: "agr_1", title: "支付接口错误率升高", state: "ACTIVE", storm_state: "STORM",
  severity: "high", service: "payment-api", environment: "production", symptom: "errors",
  active_count: 101, total_count: 101, impacted_resource_count: 101,
  first_observed_at: "2026-08-26T08:00:00Z", last_observed_at: "2026-08-26T08:01:00Z",
  explanation: "服务、环境和症状一致，已归入同一告警组。",
  incident: { id: "inc_1", title: "支付服务异常", state: "DETECTED", severity: "high" },
};

describe("组优先告警中心", () => {
  it("默认展示告警组并能读取第 101 条成员和进入事故", async () => {
    fetchAlertGroups.mockResolvedValue({ items: [group], total: 1, limit: 50, offset: 0 });
    fetchAlertGroupSummary.mockResolvedValue({ scope: "CURRENT_AND_WINDOW", current: { active_events: 1, severe_events: 1, active_alerts: 101, storm_events: 1, pending_jobs: 0 }, history: { window: "24h", closed_events: 0, raw_alerts: 101, compression_ratio: 101, peak_rate_per_minute: 101 }, calculated_at: "2026-08-26T08:02:00Z" });
    fetchAlertGroupOverview.mockResolvedValue({ group, reason_codes: [], source_distribution: [{ name: "生产 Alertmanager", count: 101 }], severity_distribution: [{ name: "high", count: 101 }], impacted_resources: [], incident_decision: { status: "INCIDENT_CREATED", label: "已创建事故", explanation: "严重生产事件需要处置", incident_id: "inc_1" }, grouping: { total_score: 92, reasons: ["same_service"], explanation: "服务、时间和文本均高度相似", dimensions: [] }, timeline: [] });
    fetchAlertGroupMembers.mockResolvedValueOnce({ items: [{ id: "alt_1", title: "实例 1", state: "ACTIVE", severity: "high", source_name: "生产 Alertmanager", last_observed_at: "2026-08-26T08:01:00Z", reason_code: "same", version: 1 }], total: 101, limit: 100, offset: 0 }).mockResolvedValueOnce({ items: [{ id: "alt_101", title: "实例 101", state: "ACTIVE", severity: "high", source_name: "生产 Alertmanager", last_observed_at: "2026-08-26T08:01:00Z", reason_code: "same", version: 1 }], total: 101, limit: 100, offset: 100 });

    const wrapper = mount(AlertGroupCenter); await flushPromises();
    expect(wrapper.get('[role="tab"][aria-selected="true"]').text()).toBe("当前事件");
    expect(wrapper.text()).toContain("已创建事故");
    expect(wrapper.text()).not.toContain("待关联事故");
    expect(wrapper.text()).toContain("101 条原始告警");
    expect(wrapper.text()).toContain("告警风暴");
    await wrapper.get('[data-testid="member-next-page"]').trigger("click"); await flushPromises();
    expect(wrapper.text()).toContain("实例 101");
    await wrapper.get('[data-testid="open-group-incident"]').trigger("click");
    expect(wrapper.emitted("open-incident")[0]).toEqual(["inc_1"]);
  });
});
