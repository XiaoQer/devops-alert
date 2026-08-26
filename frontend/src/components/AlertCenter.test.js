import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import { fetchAlertOverview, fetchAlerts, fetchAlertSummary } from "../api/alerts";
import { apiAlert, apiOverview } from "../test-fixtures/alerts";
import AlertCenter from "./AlertCenter.vue";

vi.mock("../api/alerts", () => ({ fetchAlertOverview: vi.fn(), fetchAlerts: vi.fn(), fetchAlertSummary: vi.fn() }));

describe("真实告警中心页面", () => {
  it("展示实际检测结果、系统处理过程并能进入关联事故", async () => {
    fetchAlerts.mockResolvedValue({ items: [apiAlert()], total: 1, limit: 50, offset: 0 });
    fetchAlertSummary.mockResolvedValue({ window: "24h", active: 1, severe_active: 1, resolved: 0, unlinked_active: 0, by_source: [], calculated_at: "2026-08-26T08:00:00Z" });
    fetchAlertOverview.mockResolvedValue(apiOverview());
    const wrapper = mount(AlertCenter); await flushPromises();
    expect(wrapper.text()).toContain("实际错误率达到 18.4%");
    expect(wrapper.text()).toContain("告警已认证接入");
    await wrapper.get('[data-testid="open-linked-incident"]').trigger("click");
    expect(wrapper.emitted("open-incident")[0]).toEqual(["inc_1"]);
  });

  it("展示真实总数和翻页入口", async () => {
    fetchAlerts.mockResolvedValue({ items: [apiAlert()], total: 101, limit: 50, offset: 0 });
    fetchAlertSummary.mockResolvedValue({ window: "24h", active: 101, severe_active: 101, resolved: 0, unlinked_active: 0, by_source: [], calculated_at: "2026-08-26T08:00:00Z" });
    fetchAlertOverview.mockResolvedValue(apiOverview());

    const wrapper = mount(AlertCenter); await flushPromises();

    expect(wrapper.text()).toContain("第 1–1 条，共 101 条");
    expect(wrapper.get('[data-testid="alert-next-page"]').attributes("disabled")).toBeUndefined();
  });
});
