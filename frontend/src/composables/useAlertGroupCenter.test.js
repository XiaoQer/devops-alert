import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { confirmAlertGroupMember, fetchAlertGroupMembers, fetchAlertGroupOverview, fetchAlertGroupPendingMembers, fetchAlertGroups, fetchAlertGroupSummary, mergeAlertGroups, splitAlertGroupMembers } from "../api/alertGroups";
import { useAlertGroupCenter } from "./useAlertGroupCenter";

vi.mock("../api/alertGroups", () => ({
  fetchAlertGroupMembers: vi.fn(), fetchAlertGroupOverview: vi.fn(),
  fetchAlertGroupPendingMembers: vi.fn(), confirmAlertGroupMember: vi.fn(),
  splitAlertGroupMembers: vi.fn(), mergeAlertGroups: vi.fn(),
  fetchAlertGroups: vi.fn(), fetchAlertGroupSummary: vi.fn(),
}));

const group = {
  id: "agr_1", title: "支付异常", state: "ACTIVE", storm_state: "STORM", severity: "high",
  service: "payment-api", environment: "production", symptom: "errors", active_count: 101,
  total_count: 101, impacted_resource_count: 101, first_observed_at: "2026-08-26T08:00:00Z",
  last_observed_at: "2026-08-26T08:01:00Z", explanation: "归组原因", incident: null,
  version: 1,
};
const overview = { group, reason_codes: [], source_distribution: [], severity_distribution: [], impacted_resources: [], incident_decision: { status: "NOT_EVALUATED", label: "尚未进行事故判定", explanation: "尚未进入流程", incident_id: null }, timeline: [] };
const summary = { scope: "CURRENT_AND_WINDOW", current: { active_events: 1, severe_events: 1, active_alerts: 101, storm_events: 1, pending_jobs: 0 }, history: { window: "24h", closed_events: 0, raw_alerts: 101, compression_ratio: 101, peak_rate_per_minute: 101 } };
const Harness = defineComponent({ setup: () => useAlertGroupCenter(), template: "<div />" });

beforeEach(() => {
  vi.clearAllMocks();
  fetchAlertGroupSummary.mockResolvedValue(summary);
  fetchAlertGroups.mockResolvedValue({ items: [group], total: 1, limit: 50, offset: 0 });
  fetchAlertGroupOverview.mockResolvedValue(overview);
  fetchAlertGroupMembers.mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
  fetchAlertGroupPendingMembers.mockResolvedValue({ items: [{ id: "alt_pending", title: "数据库锁等待", total_score: 65, reason: "需要人工确认", source_name: "生产告警" }], total: 1, limit: 50, offset: 0 });
  confirmAlertGroupMember.mockResolvedValue({ operation_id: "aeo_1" });
  splitAlertGroupMembers.mockResolvedValue({ operation_id: "aeo_2" });
  mergeAlertGroups.mockResolvedValue({ operation_id: "aeo_3" });
});

describe("告警组中心状态", () => {
  it("后端失败时展示安全错误且不填充演示数据", async () => {
    fetchAlertGroups.mockRejectedValue({ userMessage: "告警组读取失败" });
    const wrapper = mount(Harness); await flushPromises();
    expect(wrapper.vm.listState).toBe("error");
    expect(wrapper.vm.listError).toBe("告警组读取失败");
    expect(wrapper.vm.groups).toEqual([]);
  });

  it("组内翻页不改变告警组分页位置", async () => {
    fetchAlertGroupMembers.mockResolvedValueOnce({ items: [{ id: "a1", title: "告警 1", state: "ACTIVE", severity: "high", source_name: "来源" }], total: 101, limit: 100, offset: 0 }).mockResolvedValueOnce({ items: [{ id: "a101", title: "告警 101", state: "ACTIVE", severity: "high", source_name: "来源" }], total: 101, limit: 100, offset: 100 });
    const wrapper = mount(Harness); await flushPromises();
    await wrapper.vm.goMemberNext(); await flushPromises();
    expect(wrapper.vm.memberRangeStart).toBe(101);
    expect(wrapper.vm.rangeStart).toBe(1);
    expect(fetchAlertGroupMembers.mock.calls[1][1]).toEqual({ limit: 100, offset: 100 });
  });

  it("默认读取当前事件并可切换到待确认事件", async () => {
    const wrapper = mount(Harness); await flushPromises();
    expect(fetchAlertGroups.mock.calls[0][0].view).toBe("current");
    wrapper.vm.view = "pending";
    await new Promise((resolve) => setTimeout(resolve, 300)); await flushPromises();
    expect(fetchAlertGroups.mock.calls.at(-1)[0].view).toBe("pending");
  });

  it("确认待确认成员后重新读取真实事件", async () => {
    const wrapper = mount(Harness); await flushPromises();
    const loadedBefore = fetchAlertGroupOverview.mock.calls.length;
    await wrapper.vm.confirmPending("alt_pending", "已核对调用链");
    await flushPromises();
    expect(confirmAlertGroupMember).toHaveBeenCalledWith(
      "agr_1",
      "alt_pending",
      { expected_version: 1, reason: "已核对调用链" },
      expect.any(String),
    );
    expect(fetchAlertGroupOverview.mock.calls.length).toBeGreaterThan(loadedBefore);
  });

  it("拆分成员和合并事件均使用当前版本并刷新事实", async () => {
    const wrapper = mount(Harness); await flushPromises();
    await wrapper.vm.splitMembers(["alt_1"], "属于独立故障");
    expect(splitAlertGroupMembers).toHaveBeenCalledWith(
      "agr_1",
      { expected_version: 1, alert_ids: ["alt_1"], reason: "属于独立故障" },
      expect.any(String),
    );
    await wrapper.vm.mergeGroup("agr_2", "属于同一次故障");
    expect(mergeAlertGroups).toHaveBeenCalledWith(
      "agr_1",
      { expected_version: 1, source_group_id: "agr_2", reason: "属于同一次故障" },
      expect.any(String),
    );
  });
});
