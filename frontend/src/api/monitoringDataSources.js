import { requestJson } from "./request";

const messages = {
  unavailable: "监控数据源服务暂时不可用，请稍后重试",
  error: "监控数据源操作未完成，请检查配置后重试",
};

export function fetchMonitoringDataSources(options = {}) {
  return requestJson(
    "/api/v1/monitoring-data-sources",
    { signal: options.signal },
    messages,
  );
}

function write(path, method, body, options = {}) {
  return requestJson(
    path,
    {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: options.signal,
    },
    messages,
  );
}

export function createMonitoringDataSource(command, options = {}) {
  return write("/api/v1/monitoring-data-sources", "POST", command, options);
}

export function updateMonitoringDataSource(sourceId, command, options = {}) {
  return write(
    `/api/v1/monitoring-data-sources/${encodeURIComponent(sourceId)}`,
    "PATCH",
    command,
    options,
  );
}

export function testMonitoringDataSource(sourceId, options = {}) {
  return write(
    `/api/v1/monitoring-data-sources/${encodeURIComponent(sourceId)}/test`,
    "POST",
    undefined,
    options,
  );
}
