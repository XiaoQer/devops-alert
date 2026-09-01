import { requestJson } from "./request";

const readMessages = {
  unavailable: "Incident 暂时无法读取，请稍后重试",
  error: "Incident 暂时无法读取，请稍后重试",
};
const writeMessages = {
  unavailable: "Incident 服务暂时不可用，本次操作结果未知",
  error: "Incident 操作未完成，请稍后重试",
};
const bounded = (value, fallback) => Math.min(Math.max(value ?? fallback, 1), 100);

export function fetchIncidents(filters = {}, options = {}) {
  const params = new URLSearchParams();
  for (const state of filters.states ?? []) {
    if (state) params.append("state", state);
  }
  if (filters.environment) params.set("environment", filters.environment);
  if (filters.severity) params.set("severity", filters.severity);
  if (filters.search?.trim()) params.set("search", filters.search.trim());
  params.set("limit", String(bounded(filters.limit, 50)));
  params.set("offset", String(Math.max(filters.offset ?? 0, 0)));
  return requestJson(`/api/v1/incidents?${params}`, { signal: options.signal }, readMessages);
}

export function fetchIncident(incidentId, options = {}) {
  return requestJson(`/api/v1/incidents/${encodeURIComponent(incidentId)}`, { signal: options.signal }, readMessages);
}

function mutate(path, body, idempotencyKey, options = {}) {
  return requestJson(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(body),
    signal: options.signal,
  }, writeMessages);
}

export function acknowledgeIncident(incidentId, expectedVersion, idempotencyKey, options = {}) {
  return mutate(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/acknowledge`,
    { expected_version: expectedVersion },
    idempotencyKey,
    options,
  );
}

export function resolveIncident(incidentId, expectedVersion, resolutionSummary, idempotencyKey, options = {}) {
  return mutate(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/resolve`,
    { expected_version: expectedVersion, resolution_summary: resolutionSummary },
    idempotencyKey,
    options,
  );
}
