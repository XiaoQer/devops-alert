import { requestJson } from "./request";

const alertMessages = {
  unavailable: "告警服务暂时不可用，请稍后重试",
  error: "告警数据暂时不可用，请稍后重试",
};

export function fetchAlerts(filters = {}, options = {}) {
  const params = new URLSearchParams();
  for (const key of ["alert_source_id", "state", "severity", "service", "environment", "incident_linked", "observed_from", "observed_to"]) {
    if (filters[key] !== undefined && filters[key] !== null && filters[key] !== "") params.set(key, String(filters[key]));
  }
  if (filters.query?.trim()) params.set("query", filters.query.trim());
  params.set("limit", String(Math.min(Math.max(filters.limit ?? 50, 1), 100)));
  params.set("offset", String(Math.max(filters.offset ?? 0, 0)));
  return requestJson(`/api/v1/alerts?${params.toString()}`, { signal: options.signal }, alertMessages);
}

export function fetchAlertSummary(window = "24h", options = {}) {
  return requestJson(`/api/v1/alerts/summary?window=${encodeURIComponent(window)}`, { signal: options.signal }, alertMessages);
}

export function fetchAlertOverview(alertId, options = {}) {
  return requestJson(`/api/v1/alerts/${encodeURIComponent(alertId)}/overview`, { signal: options.signal }, alertMessages);
}
