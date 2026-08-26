import { requestJson } from "./request";

const messages = {
  unavailable: "告警组服务暂时不可用，请稍后重试",
  error: "告警组数据暂时不可用，请稍后重试",
};

function pageParams(filters, keys) {
  const params = new URLSearchParams();
  for (const key of keys) {
    if (filters[key] !== undefined && filters[key] !== null && filters[key] !== "") {
      params.set(key, String(filters[key]));
    }
  }
  if (filters.query?.trim()) params.set("query", filters.query.trim());
  params.set("limit", String(Math.min(Math.max(filters.limit ?? 50, 1), 100)));
  params.set("offset", String(Math.min(Math.max(filters.offset ?? 0, 0), 10_000)));
  return params;
}

export function fetchAlertGroups(filters = {}, options = {}) {
  const params = pageParams(filters, [
    "state", "severity", "service", "environment", "storm_state", "incident_linked",
    "observed_from", "observed_to",
  ]);
  return requestJson(`/api/v1/alert-groups?${params.toString()}`, { signal: options.signal }, messages);
}

export function fetchAlertGroupSummary(window = "24h", options = {}) {
  return requestJson(`/api/v1/alert-groups/summary?window=${encodeURIComponent(window)}`, { signal: options.signal }, messages);
}

export function fetchAlertGroupOverview(groupId, options = {}) {
  return requestJson(`/api/v1/alert-groups/${encodeURIComponent(groupId)}/overview`, { signal: options.signal }, messages);
}

export function fetchAlertGroupMembers(groupId, filters = {}, options = {}) {
  const params = pageParams(filters, []);
  params.delete("query");
  return requestJson(`/api/v1/alert-groups/${encodeURIComponent(groupId)}/alerts?${params.toString()}`, { signal: options.signal }, messages);
}

export function fetchIncidentAlertGroups(incidentId, filters = {}, options = {}) {
  const params = pageParams(filters, []);
  params.delete("query");
  return requestJson(`/api/v1/incidents/${encodeURIComponent(incidentId)}/alert-groups?${params.toString()}`, { signal: options.signal }, messages);
}
