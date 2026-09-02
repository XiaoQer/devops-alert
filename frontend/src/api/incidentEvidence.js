import { requestJson } from "./request";

const messages = {
  unavailable: "监控取证暂时无法读取，请稍后重试",
  error: "监控取证操作未完成，请稍后重试",
};

export function fetchEvidenceRuns(incidentId, options = {}) {
  const limit = Math.min(Math.max(options.limit ?? 50, 1), 50);
  const offset = Math.max(options.offset ?? 0, 0);
  return requestJson(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/evidence-runs?limit=${limit}&offset=${offset}`,
    { signal: options.signal },
    messages,
  );
}

export function fetchEvidenceRun(incidentId, runId, options = {}) {
  return requestJson(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/evidence-runs/${encodeURIComponent(runId)}`,
    { signal: options.signal },
    messages,
  );
}

export function requestEvidenceRun(incidentId, idempotencyKey, options = {}) {
  return requestJson(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/evidence-runs`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      signal: options.signal,
    },
    messages,
  );
}
