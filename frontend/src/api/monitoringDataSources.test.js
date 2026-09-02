import { beforeEach, expect, it, vi } from "vitest";

import {
  createMonitoringDataSource,
  fetchMonitoringDataSources,
  testMonitoringDataSource,
  updateMonitoringDataSource,
} from "./monitoringDataSources";

beforeEach(() => {
  vi.restoreAllMocks();
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: true,
    headers: new Headers({ "Content-Type": "application/json" }),
    json: async () => ({ items: [], total: 0 }),
  });
});

it("监控数据源请求使用受保护的固定接口", async () => {
  const command = {
    name: "测试 Prometheus",
    environment: "testing",
    source_type: "PROMETHEUS",
    base_url: "http://prometheus:9090",
    credential_env_key: null,
    field_mapping: {},
    verify_tls: true,
    enabled: true,
  };

  await fetchMonitoringDataSources();
  await createMonitoringDataSource(command);
  await updateMonitoringDataSource("mds_1", { ...command, expected_version: 1 });
  await testMonitoringDataSource("mds_1");

  expect(fetch).toHaveBeenNthCalledWith(
    1,
    "/api/v1/monitoring-data-sources",
    expect.objectContaining({ headers: { Accept: "application/json" } }),
  );
  expect(fetch).toHaveBeenNthCalledWith(
    2,
    "/api/v1/monitoring-data-sources",
    expect.objectContaining({ method: "POST", body: JSON.stringify(command) }),
  );
  expect(fetch).toHaveBeenNthCalledWith(
    3,
    "/api/v1/monitoring-data-sources/mds_1",
    expect.objectContaining({ method: "PATCH" }),
  );
  expect(fetch).toHaveBeenNthCalledWith(
    4,
    "/api/v1/monitoring-data-sources/mds_1/test",
    expect.objectContaining({ method: "POST" }),
  );
});
