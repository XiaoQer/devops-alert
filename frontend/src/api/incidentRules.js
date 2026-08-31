import { requestJson } from "./request";

const readMessages = {
  unavailable: "Incident 规则暂时无法读取，请稍后重试",
  error: "Incident 规则暂时无法读取，请稍后重试",
};
const writeMessages = {
  unavailable: "Incident 规则服务暂时不可用，本次操作结果未知",
  error: "Incident 规则操作未完成，请稍后重试",
};
const bounded = (value, fallback) => Math.min(Math.max(value ?? fallback, 1), 100);

export function fetchIncidentRules(filters = {}, options = {}) {
  const params = new URLSearchParams();
  if (filters.state) params.set("state", filters.state);
  params.set("limit", String(bounded(filters.limit, 50)));
  params.set("offset", String(Math.max(filters.offset ?? 0, 0)));
  return requestJson(`/api/v1/incident-rules?${params}`, { signal: options.signal }, readMessages);
}

export function fetchIncidentRule(ruleId, options = {}) {
  return requestJson(`/api/v1/incident-rules/${encodeURIComponent(ruleId)}`, { signal: options.signal }, readMessages);
}

function write(path, method, body, idempotencyKey, options = {}) {
  const headers = { "Content-Type": "application/json" };
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  return requestJson(
    path,
    { method, headers, body: JSON.stringify(body), signal: options.signal },
    writeMessages,
  );
}

export function createIncidentRule(command, idempotencyKey, options = {}) {
  return write("/api/v1/incident-rules", "POST", command, idempotencyKey, options);
}

export function updateIncidentRule(ruleId, command, idempotencyKey, options = {}) {
  return write(`/api/v1/incident-rules/${encodeURIComponent(ruleId)}`, "PATCH", command, idempotencyKey, options);
}

export function deleteIncidentRule(ruleId, expectedVersion, idempotencyKey, options = {}) {
  return write(`/api/v1/incident-rules/${encodeURIComponent(ruleId)}`, "DELETE", { expected_version: expectedVersion }, idempotencyKey, options);
}

export function dryRunIncidentRule(ruleId, command, options = {}) {
  return write(`/api/v1/incident-rules/${encodeURIComponent(ruleId)}/dry-runs`, "POST", command, null, options);
}

export function publishIncidentRule(ruleId, expectedVersion, idempotencyKey, options = {}) {
  return write(`/api/v1/incident-rules/${encodeURIComponent(ruleId)}/publish`, "POST", { expected_version: expectedVersion }, idempotencyKey, options);
}

export function disableIncidentRule(ruleId, expectedVersion, idempotencyKey, options = {}) {
  return write(`/api/v1/incident-rules/${encodeURIComponent(ruleId)}/disable`, "POST", { expected_version: expectedVersion }, idempotencyKey, options);
}

export function copyIncidentRule(ruleId, name, idempotencyKey, options = {}) {
  return write(`/api/v1/incident-rules/${encodeURIComponent(ruleId)}/copies`, "POST", { name }, idempotencyKey, options);
}
