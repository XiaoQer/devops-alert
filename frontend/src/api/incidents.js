export class IncidentApiError extends Error {
  constructor(code, userMessage, status = 0) {
    super(userMessage);
    this.name = "IncidentApiError";
    this.code = code;
    this.userMessage = userMessage;
    this.status = status;
  }
}

async function requestJson(path, options = {}) {
  try {
    const response = await fetch(path, {
      ...options,
      headers: { Accept: "application/json", ...(options.headers ?? {}) },
    });
    const isJson = response.headers.get("Content-Type")?.includes("application/json");
    const body = isJson ? await response.json() : null;
    if (!response.ok) {
      throw new IncidentApiError(
        typeof body?.code === "string" ? body.code : "incident_api_error",
        typeof body?.message === "string"
          ? body.message
          : "事故数据暂时不可用，请稍后重试",
        response.status,
      );
    }
    return body;
  } catch (error) {
    if (error?.name === "AbortError" || error instanceof IncidentApiError) throw error;
    throw new IncidentApiError(
      "incident_api_unavailable",
      "事故数据暂时不可用，请稍后重试",
    );
  }
}

export function fetchIncidents(filters = {}, options = {}) {
  const params = new URLSearchParams();
  if (filters.environment && filters.environment !== "all") {
    params.set("environment", filters.environment);
  }
  if (filters.state) params.set("state", filters.state);
  if (filters.query?.trim()) params.set("query", filters.query.trim());
  params.set("limit", String(filters.limit ?? 100));
  params.set("offset", String(filters.offset ?? 0));
  return requestJson(`/api/v1/incidents?${params.toString()}`, { signal: options.signal });
}

export function fetchIncidentOverview(incidentId, options = {}) {
  return requestJson(`/api/v1/incidents/${encodeURIComponent(incidentId)}/overview`, {
    signal: options.signal,
  });
}

const incidentActions = new Set([
  "claim",
  "release",
  "transitions",
  "notes",
  "resolve",
  "reopen",
  "close",
]);

export function executeIncidentAction(
  incidentId,
  action,
  command,
  idempotencyKey,
  options = {},
) {
  if (!incidentActions.has(action)) {
    throw new IncidentApiError("invalid_incident_action", "未知的事故操作");
  }
  if (typeof idempotencyKey !== "string" || !idempotencyKey.trim()) {
    throw new IncidentApiError("invalid_idempotency_key", "本次操作缺少安全重试标识");
  }
  return requestJson(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/${action}`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify(command),
      signal: options.signal,
    },
  );
}

export function claimIncident(incidentId, options = {}) {
  return requestJson(`/api/v1/incidents/${encodeURIComponent(incidentId)}/claim`, {
    method: "POST",
    signal: options.signal,
  });
}
