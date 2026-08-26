import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchAlertOverview, fetchAlerts, fetchAlertSummary } from "../api/alerts";
import { ApiError } from "../api/request";
import { apiAlert, apiOverview } from "../test-fixtures/alerts";
import { useAlertCenter } from "./useAlertCenter";

vi.mock("../api/alerts", () => ({ fetchAlertOverview: vi.fn(), fetchAlerts: vi.fn(), fetchAlertSummary: vi.fn() }));

function mountCenter() {
  let center;
  const wrapper = mount(defineComponent({ setup() { center = useAlertCenter(); return () => h("div"); } }));
  return { wrapper, get center() { return center; } };
}

beforeEach(() => {
  fetchAlerts.mockResolvedValue({ items: [apiAlert()], total: 1, limit: 50, offset: 0 });
  fetchAlertSummary.mockResolvedValue({ window: "24h", active: 1, severe_active: 1, resolved: 0, unlinked_active: 0, by_source: [], calculated_at: "2026-08-26T08:00:00Z" });
  fetchAlertOverview.mockResolvedValue(apiOverview());
});
afterEach(() => vi.clearAllMocks());

describe("告警中心状态", () => {
  it("加载真实列表、概况和首条详情", async () => {
    const mounted = mountCenter(); await flushPromises();
    expect(mounted.center.alerts.value).toHaveLength(1);
    expect(mounted.center.summary.value.active).toBe(1);
    expect(mounted.center.detail.value.resultText).toContain("18.4%");
    mounted.wrapper.unmount();
  });

  it("详情失败时保留已读取列表且不注入演示数据", async () => {
    const mounted = mountCenter(); await flushPromises();
    fetchAlertOverview.mockRejectedValueOnce(new ApiError("api_unavailable", "暂时不可用"));
    await mounted.center.selectAlert("alt_real");
    expect(mounted.center.alerts.value).toHaveLength(1);
    expect(mounted.center.detailState.value).toBe("error");
    expect(mounted.center.detail.value).toBeNull();
    mounted.wrapper.unmount();
  });
});
