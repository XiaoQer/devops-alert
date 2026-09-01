import { requestJson } from "./request";

const readMessages = {
  unavailable: "飞书通知配置暂时无法读取，请稍后重试",
  error: "飞书通知配置暂时无法读取，请稍后重试",
};
const writeMessages = {
  unavailable: "飞书通知配置服务暂时不可用，本次操作结果未知",
  error: "飞书通知配置未保存，请稍后重试",
};

export function fetchIncidentNotificationRoutes(options = {}) {
  return requestJson("/api/v1/incident-notification-routes", { signal: options.signal }, readMessages);
}

function mutate(path, method, command, idempotencyKey, options = {}) {
  return requestJson(path, {
    method,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(command),
    signal: options.signal,
  }, writeMessages);
}

export function createIncidentNotificationRoute(command, idempotencyKey, options = {}) {
  return mutate("/api/v1/incident-notification-routes", "POST", command, idempotencyKey, options);
}

export function updateIncidentNotificationRoute(routeId, command, expectedVersion, idempotencyKey, options = {}) {
  return mutate(
    `/api/v1/incident-notification-routes/${encodeURIComponent(routeId)}`,
    "PATCH",
    { ...command, expected_version: expectedVersion },
    idempotencyKey,
    options,
  );
}
