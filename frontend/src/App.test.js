import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App.vue";
import { fetchAlert, fetchAlerts, fetchAlertTrend } from "./api/alerts";
import { fetchAlertSources } from "./api/alertSources";
import { fetchIncidentRules } from "./api/incidentRules";
import { fetchIncidents } from "./api/incidents";
import { fetchMonitoringDataSources } from "./api/monitoringDataSources";
import styles from "./styles.css?inline";

vi.mock("./api/alerts", () => ({ fetchAlert: vi.fn(), fetchAlerts: vi.fn(), fetchAlertTrend: vi.fn() }));
vi.mock("./api/alertSources", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchAlertSources: vi.fn(),
}));
vi.mock("./api/incidentRules", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchIncidentRules: vi.fn(),
}));
vi.mock("./api/incidents", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchIncidents: vi.fn(),
}));
vi.mock("./api/monitoringDataSources", () => ({
  fetchMonitoringDataSources: vi.fn(),
  createMonitoringDataSource: vi.fn(),
  updateMonitoringDataSource: vi.fn(),
  testMonitoringDataSource: vi.fn(),
}));

const alert = {
  id: "alt_1", alert_name: "MySQLRowLockWaitActive", summary: "当前检测到活跃行锁等待",
  state: "ACTIVE", severity: "high", service: null,
  environment: "production", entity_type: "INSTANCE", entity_display_name: "mysql:3306",
  source: { id: "src_1", name: "生产 Prometheus", source_type: "ALERTMANAGER", management_type: "USER_MANAGED" },
  episode_started_at: "2026-08-28T08:00:00Z", first_received_at: "2026-08-28T08:00:01Z",
  last_received_at: "2026-08-28T08:00:01Z", resolved_at: null, duration_seconds: null,
};

beforeEach(() => {
  fetchAlerts.mockResolvedValue({ items: [alert], total: 1, limit: 50, offset: 0 });
  fetchAlert.mockResolvedValue({ ...alert, description: "当前检测到活跃行锁等待" });
  fetchAlertTrend.mockResolvedValue({
    range: "1h", basis: "first_received_at", bucket_seconds: 300,
    window_start: "2026-08-28T07:00:00Z", window_end: "2026-08-28T08:00:00Z",
    series: [{ source: alert.source, points: [{ timestamp: "2026-08-28T07:00:00Z", count: 1 }] }],
  });
  fetchAlertSources.mockResolvedValue({ items: [], total: 0 });
  fetchIncidentRules.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  fetchIncidents.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  fetchMonitoringDataSources.mockResolvedValue({ items: [], total: 0 });
});

describe("最小告警接入平台", () => {
  it("展示告警、Incident、规则、告警接入源和监控数据源五个入口", async () => {
    const wrapper = mount(App);
    await flushPromises();
    const navigation = wrapper.get('[aria-label="主导航"]');
    expect(navigation.findAll("button")).toHaveLength(5);
    expect(navigation.text()).toContain("告警中心");
    expect(navigation.text()).toContain("Incident 中心");
    expect(navigation.text()).toContain("Incident 规则");
    expect(navigation.text()).toContain("接入源管理");
    expect(navigation.text()).toContain("监控数据源");
    for (const removed of ["事故中心", "批次", "关联任务", "服务目录", "AI"])
      expect(navigation.text()).not.toContain(removed);
  });

  it("默认展示真实告警并可切换到接入源管理", async () => {
    const wrapper = mount(App);
    await flushPromises();
    expect(wrapper.get('[data-testid="alert-center"]').text()).toContain("MySQLRowLockWaitActive");
    await wrapper.get('[data-testid="nav-alert-sources"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="alert-source-center"]').exists()).toBe(true);
  });

  it("告警页面只展示一个页面标题", async () => {
    const wrapper = mount(App);
    await flushPromises();
    expect(wrapper.findAll("h1")).toHaveLength(1);
    expect(wrapper.findAll("h2")).toHaveLength(0);
    expect(wrapper.get("h1").text()).toBe("告警中心");
  });

  it("告警列表被约束在主区域内并独立滚动", async () => {
    const wrapper = mount(App, { attachTo: document.body });
    await flushPromises();
    const style = document.createElement("style");
    style.textContent = styles;
    document.head.append(style);

    const pageStyle = getComputedStyle(wrapper.get('[data-testid="alert-center"]').element);
    const workspaceStyle = getComputedStyle(wrapper.get(".raw-alert-workspace").element);
    const listStyle = getComputedStyle(wrapper.get('[aria-label="告警列表"]').element);
    const scrollStyle = getComputedStyle(wrapper.get(".raw-alert-scroll").element);
    const pagerStyle = getComputedStyle(wrapper.get(".raw-alert-pagination").element);

    expect(pageStyle.height).toBe("100%");
    expect(pageStyle.overflow).toBe("hidden");
    expect(workspaceStyle.minHeight).toBe("0");
    expect(listStyle.overflow).toBe("hidden");
    expect(scrollStyle.overflow).toBe("auto");
    expect(pagerStyle.position).toBe("static");
    expect(getComputedStyle(wrapper.get(".minimal-topbar h1").element).fontSize).toBe("16px");
    expect(getComputedStyle(wrapper.get(".trend-range-select select").element).height).toBe("40px");
    style.remove();
    wrapper.unmount();
  });
});
