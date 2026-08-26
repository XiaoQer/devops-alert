import { requestJson } from "./request";

const readMessages = { unavailable: "告警源数据暂时不可用，请稍后重试", error: "告警源数据暂时不可用，请稍后重试" };
const writeMessages = { unavailable: "告警源服务暂时不可用，本次操作结果未知", error: "告警源操作未完成，请稍后重试" };
const bounded = (value, fallback) => Math.min(Math.max(value ?? fallback, 1), 100);

export function fetchAlertSources(filters = {}, options = {}) {
  const params = new URLSearchParams();
  if (filters.source_type) params.set("source_type", filters.source_type);
  if (filters.state) params.set("state", filters.state);
  params.set("limit", String(bounded(filters.limit, 50))); params.set("offset", String(Math.max(filters.offset ?? 0, 0)));
  return requestJson(`/api/v1/alert-sources?${params}`, { signal: options.signal }, readMessages);
}
export function fetchAlertSource(sourceId, options = {}) { return requestJson(`/api/v1/alert-sources/${encodeURIComponent(sourceId)}`, { signal: options.signal }, readMessages); }
export function fetchAlertSourceReceipts(sourceId, filters = {}, options = {}) {
  const params = new URLSearchParams({ limit: String(bounded(filters.limit, 50)), offset: String(Math.max(filters.offset ?? 0, 0)) });
  return requestJson(`/api/v1/alert-sources/${encodeURIComponent(sourceId)}/receipts?${params}`, { signal: options.signal }, readMessages);
}
function write(path, method, body, idempotencyKey, options = {}) {
  return requestJson(path, { method, headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey }, body: JSON.stringify(body), signal: options.signal }, writeMessages);
}
export function createAlertSource(command, idempotencyKey, options = {}) { return write("/api/v1/alert-sources", "POST", command, idempotencyKey, options); }
export function updateAlertSource(sourceId, command, idempotencyKey, options = {}) { return write(`/api/v1/alert-sources/${encodeURIComponent(sourceId)}`, "PATCH", command, idempotencyKey, options); }
export function rotateAlertSourceCredential(sourceId, expectedVersion, idempotencyKey, options = {}) { return write(`/api/v1/alert-sources/${encodeURIComponent(sourceId)}/credentials/rotate`, "POST", { expected_version: expectedVersion }, idempotencyKey, options); }
export function revokeAlertSourceCredential(sourceId, credentialId, expectedVersion, idempotencyKey, options = {}) { return write(`/api/v1/alert-sources/${encodeURIComponent(sourceId)}/credentials/${encodeURIComponent(credentialId)}/revoke`, "POST", { expected_version: expectedVersion }, idempotencyKey, options); }
