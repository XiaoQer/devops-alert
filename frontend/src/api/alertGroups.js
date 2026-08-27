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
    "observed_from", "observed_to", "view",
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

export function fetchAlertGroupPendingMembers(groupId, filters = {}, options = {}) {
  const params = pageParams(filters, []);
  params.delete("query");
  return requestJson(`/api/v1/alert-groups/${encodeURIComponent(groupId)}/pending-members?${params.toString()}`, { signal: options.signal }, messages);
}

export function fetchIncidentAlertGroups(incidentId, filters = {}, options = {}) {
  const params = pageParams(filters, []);
  params.delete("query");
  return requestJson(`/api/v1/incidents/${encodeURIComponent(incidentId)}/alert-groups?${params.toString()}`, { signal: options.signal }, messages);
}

function write(path, body, idempotencyKey, options = {}) {
  return requestJson(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(body),
    signal: options.signal,
  }, { ...messages, unavailable: "事件操作结果暂时未知，请使用原操作重试" });
}

export function confirmAlertGroupMember(groupId, alertId, command, key, options = {}) {
  return write(`/api/v1/alert-groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(alertId)}/confirm`, command, key, options);
}

export function splitAlertGroupMembers(groupId, command, key, options = {}) {
  return write(`/api/v1/alert-groups/${encodeURIComponent(groupId)}/members/split`, command, key, options);
}

export function mergeAlertGroups(groupId, command, key, options = {}) {
  return write(`/api/v1/alert-groups/${encodeURIComponent(groupId)}/merge`, command, key, options);
}
