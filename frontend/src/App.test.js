import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App.vue";
import { claimIncident, fetchIncidentOverview, fetchIncidents } from "./api/incidents";

vi.mock("./api/incidents", () => ({ claimIncident: vi.fn(), fetchIncidentOverview: vi.fn(), fetchIncidents: vi.fn() }));

const listItem = (overrides = {}) => ({
  id: "inc_real_1", title: "支付服务错误率升高", severity: "critical", state: "INVESTIGATING",
  service: "payment-api", environment: "production", assignee: null, owner_team: "支付平台组",
  detected_at: "2026-08-25T02:06:18Z", last_activity_at: "2026-08-25T02:09:45Z",
  alert_count: 2, version: 1, ...overrides,
});

const overview = (overrides = {}) => ({
  ...listItem(), claimed_at: null, created_at: "2026-08-25T02:06:20Z", alerts_truncated: false,
  alerts: [
    { id: "alt_1", title: "支付接口 5xx 错误率升高", state: "ACTIVE", severity: "critical", source: "alertmanager", first_observed_at: "2026-08-25T02:06:18Z", last_observed_at: "2026-08-25T02:09:45Z", version: 1 },
    { id: "alt_2", title: "支付成功率下降", state: "ACTIVE", severity: "high", source: "cloudevents", first_observed_at: "2026-08-25T02:07:12Z", last_observed_at: "2026-08-25T02:09:45Z", version: 1 },
  ],
  correlation: { outcome: "LINKED", rule_version: "correlation.v1", reason_codes: ["same_service"], explanation: "两条告警属于同一生产服务且时间相近。", created_at: "2026-08-25T02:07:13Z" },
  timeline: [
    { id: "created", kind: "incident_created", occurred_at: "2026-08-25T02:06:20Z", title: "创建事故", detail: "系统根据已持久化告警创建事故" },
    { id: "linked", kind: "alert_linked", occurred_at: "2026-08-25T02:07:12Z", title: "关联告警", detail: "支付成功率下降" },
  ], ...overrides,
});

beforeEach(() => {
  fetchIncidents.mockResolvedValue({ items: [listItem()], total: 1, limit: 100, offset: 0 });
  fetchIncidentOverview.mockResolvedValue(overview());
  claimIncident.mockResolvedValue({ id: "inc_real_1", assignee: "manual-api-client", claimed_at: "2026-08-25T02:12:00Z", version: 2 });
});
afterEach(() => { vi.clearAllMocks(); vi.useRealTimers(); });

describe("事故中心真实联调", () => {
  it("启动后展示后端事故、关联原因和关联告警", async () => {
    const wrapper = mount(App); await flushPromises();
    expect(wrapper.text()).toContain("支付服务错误率升高");
    expect(wrapper.text()).toContain("两条告警属于同一生产服务且时间相近。");
    expect(wrapper.findAll('[data-testid="related-alert-row"]')).toHaveLength(2);
  });

  it("列表失败时只展示安全错误并可重试", async () => {
    fetchIncidents.mockRejectedValueOnce({ userMessage: "事故数据暂时不可用，请稍后重试" });
    const wrapper = mount(App); await flushPromises();
    expect(wrapper.text()).toContain("事故数据暂时不可用，请稍后重试");
    expect(wrapper.text()).not.toContain("支付服务错误率升高");
    await wrapper.get('[data-testid="retry-list"]').trigger("click"); await flushPromises();
    expect(wrapper.text()).toContain("支付服务错误率升高");
  });

  it("后端无事故时展示真实空状态", async () => {
    fetchIncidents.mockResolvedValueOnce({ items: [], total: 0, limit: 100, offset: 0 });
    const wrapper = mount(App); await flushPromises();
    expect(wrapper.text()).toContain("当前没有事故");
    expect(fetchIncidentOverview).not.toHaveBeenCalled();
  });

  it("选择事故后读取对应详情", async () => {
    fetchIncidents.mockResolvedValueOnce({ items: [listItem(), listItem({ id: "inc_real_2", title: "库存服务延迟升高", service: "inventory-api" })], total: 2, limit: 100, offset: 0 });
    fetchIncidentOverview.mockResolvedValueOnce(overview()).mockResolvedValueOnce(overview({ id: "inc_real_2", title: "库存服务延迟升高", service: "inventory-api", alerts: [] }));
    const wrapper = mount(App); await flushPromises();
    await wrapper.get('[data-testid="incident-inc_real_2"] button').trigger("click"); await flushPromises();
    expect(fetchIncidentOverview).toHaveBeenLastCalledWith("inc_real_2", expect.any(Object));
    expect(wrapper.get('[data-testid="incident-title"]').text()).toContain("库存服务延迟升高");
  });

  it("认领通过后端保存并刷新真实数据", async () => {
    fetchIncidents.mockResolvedValueOnce({ items: [listItem()], total: 1, limit: 100, offset: 0 }).mockResolvedValueOnce({ items: [listItem({ assignee: "manual-api-client", version: 2 })], total: 1, limit: 100, offset: 0 });
    fetchIncidentOverview.mockResolvedValueOnce(overview()).mockResolvedValueOnce(overview({ assignee: "manual-api-client", claimed_at: "2026-08-25T02:12:00Z", version: 2 }));
    const wrapper = mount(App); await flushPromises();
    await wrapper.get('[data-testid="claim-incident"]').trigger("click"); await flushPromises();
    expect(claimIncident).toHaveBeenCalledWith("inc_real_1");
    expect(wrapper.get('[data-testid="incident-owner"]').text()).toContain("当前操作员");
    expect(wrapper.get('[data-testid="claim-incident"]').text()).toContain("已认领");
  });

  it("搜索时丢弃晚到的旧列表结果", async () => {
    vi.useFakeTimers();
    let resolveOld;
    fetchIncidents
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce({ items: [listItem({ id: "inc_new", title: "最新搜索结果" })], total: 1, limit: 100, offset: 0 });
    fetchIncidentOverview.mockResolvedValueOnce(overview({ id: "inc_new", title: "最新搜索结果" }));
    const wrapper = mount(App);
    await wrapper.get('[aria-label="搜索事故、服务或团队"]').setValue("最新");
    await vi.advanceTimersByTimeAsync(250); await flushPromises();
    expect(wrapper.text()).toContain("最新搜索结果");
    resolveOld({ items: [listItem({ id: "inc_old", title: "过期搜索结果" })], total: 1, limit: 100, offset: 0 });
    await flushPromises();
    expect(wrapper.text()).toContain("最新搜索结果");
    expect(wrapper.text()).not.toContain("过期搜索结果");
  });
});
