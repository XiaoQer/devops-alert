import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";

import { fetchIncident, fetchIncidents } from "../api/incidents";
import IncidentCenter from "./IncidentCenter.vue";

vi.mock("../api/incidents", () => ({
  fetchIncidents: vi.fn(), fetchIncident: vi.fn(), acknowledgeIncident: vi.fn(), resolveIncident: vi.fn(),
}));

const incident = { id: "inc_1", reference: "INC-20260901-001", title: "production checkout异常", state: "OPEN", severity: "critical", environment: "production", group_by: "SERVICE", group_key: "checkout", group_display_name: "checkout", incident_rule_id: "irl_1", incident_rule_version: 1, alert_count: 3, active_alert_count: 2, distinct_alert_name_count: 2, version: 1, opened_at: "2026-09-01T01:00:00Z", acknowledged_at: null, resolved_at: null, resolution_summary: null, last_alert_at: "2026-09-01T01:10:00Z", created_at: "2026-09-01T01:00:00Z", updated_at: "2026-09-01T01:10:00Z", rule_name: "支付异常", rule_summary: "5 分钟内 2 条活动告警" };

beforeEach(() => {
  vi.clearAllMocks();
  fetchIncidents.mockResolvedValue({ items: [incident], total: 1, limit: 50, offset: 0 });
  fetchIncident.mockResolvedValue({ incident, rule_name: incident.rule_name, rule_summary: incident.rule_summary, alerts: [], alerts_truncated: false, activities: [], activities_truncated: false, feishu: { configured: false, route_name: null, chat_id_masked: null, thread_bound: false, last_synced_at: null, notification_state: null, last_error_code: null } });
});

it("默认展示未解决 Incident 且列表不出现演示数据", async () => {
  const wrapper = mount(IncidentCenter);
  await flushPromises();
  expect(wrapper.text()).toContain("INC-20260901-001");
  expect(wrapper.text()).toContain("production checkout异常");
  expect(wrapper.text()).not.toContain("demo");
  expect(fetchIncidents).toHaveBeenCalledWith(expect.objectContaining({ states: ["OPEN", "ACKNOWLEDGED"] }), expect.any(Object));
});

it("点击列表后展示真实三栏详情", async () => {
  const wrapper = mount(IncidentCenter);
  await flushPromises();
  await wrapper.get('[data-testid="incident-row"]').trigger("click");
  await flushPromises();
  expect(wrapper.get('[data-testid="incident-detail"]').text()).toContain("当前情况");
  expect(wrapper.get('[data-testid="incident-detail"]').text()).toContain("关联告警");
  expect(wrapper.get('[data-testid="incident-detail"]').text()).toContain("处置与飞书");
});
