import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";

import {
  createMonitoringDataSource,
  fetchMonitoringDataSources,
  testMonitoringDataSource,
  updateMonitoringDataSource,
} from "../api/monitoringDataSources";
import MonitoringDataSourceManager from "./MonitoringDataSourceManager.vue";

vi.mock("../api/monitoringDataSources", () => ({
  createMonitoringDataSource: vi.fn(),
  fetchMonitoringDataSources: vi.fn(),
  testMonitoringDataSource: vi.fn(),
  updateMonitoringDataSource: vi.fn(),
}));

const prometheus = {
  id: "mds_1",
  name: "测试 Prometheus",
  environment: "testing",
  source_type: "PROMETHEUS",
  base_url: "http://prometheus:9090",
  credential_env_key: "II_PROMETHEUS_TOKEN",
  credential_configured: true,
  field_mapping: {},
  verify_tls: true,
  enabled: true,
  version: 1,
  last_test_state: "AVAILABLE",
  last_test_latency_ms: 18,
  last_compatible_version: "2.54.1",
  last_test_error_code: null,
  last_tested_at: "2026-09-02T10:00:00Z",
  created_at: "2026-09-02T09:00:00Z",
  updated_at: "2026-09-02T09:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  fetchMonitoringDataSources.mockResolvedValue({ items: [prometheus], total: 1 });
  createMonitoringDataSource.mockResolvedValue(prometheus);
  updateMonitoringDataSource.mockResolvedValue(prometheus);
  testMonitoringDataSource.mockResolvedValue({
    state: "AVAILABLE",
    latency_ms: 18,
    compatible_version: "2.54.1",
    error_code: null,
  });
});

it("集中展示连接状态且不提供密钥值输入框", async () => {
  const wrapper = mount(MonitoringDataSourceManager);
  await flushPromises();

  expect(wrapper.text()).toContain("测试 Prometheus");
  expect(wrapper.text()).toContain("连接正常");
  expect(wrapper.text()).toContain("2.54.1");
  expect(wrapper.text()).toContain("18 ms");
  expect(wrapper.find('input[type="password"]').exists()).toBe(false);
  await wrapper.get(".monitoring-source-card footer .button").trigger("click");
  expect(wrapper.text()).toContain("环境变量名称");
});

it("执行真实检测后重新读取持久化状态", async () => {
  const wrapper = mount(MonitoringDataSourceManager);
  await flushPromises();

  await wrapper.get('[data-testid="test-source-mds_1"]').trigger("click");
  await flushPromises();

  expect(testMonitoringDataSource).toHaveBeenCalledWith("mds_1");
  expect(fetchMonitoringDataSources).toHaveBeenCalledTimes(2);
  expect(wrapper.text()).toContain("连接正常");
});

it("新增数据源只提交地址和密钥环境变量引用", async () => {
  fetchMonitoringDataSources.mockResolvedValue({ items: [], total: 0 });
  const wrapper = mount(MonitoringDataSourceManager);
  await flushPromises();

  await wrapper.get('[data-testid="new-monitoring-source"]').trigger("click");
  await wrapper.get('[name="name"]').setValue("测试 Prometheus");
  await wrapper.get('[name="base_url"]').setValue("http://prometheus:9090");
  await wrapper.get('[name="credential_env_key"]').setValue("II_PROMETHEUS_TOKEN");
  await wrapper.get("form").trigger("submit");
  await flushPromises();

  expect(createMonitoringDataSource).toHaveBeenCalledWith({
    name: "测试 Prometheus",
    environment: "testing",
    source_type: "PROMETHEUS",
    base_url: "http://prometheus:9090",
    credential_env_key: "II_PROMETHEUS_TOKEN",
    field_mapping: {},
    verify_tls: true,
    enabled: true,
  });
});
