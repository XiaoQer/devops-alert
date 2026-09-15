import { requestJson } from "./request";

const readMessages = {
  unavailable: "智能分析暂时无法读取，请稍后重试",
  error: "智能分析暂时无法读取，请稍后重试",
};
const writeMessages = {
  unavailable: "智能分析服务暂时不可用，本次操作结果未知",
  error: "智能分析未能启动，请稍后重试",
};
const bounded = (value, fallback) => Math.min(Math.max(value ?? fallback, 1), 50);

export function fetchDiagnosisRuns(incidentId, options = {}) {
  const limit = bounded(options.limit, 50);
  const offset = Math.max(options.offset ?? 0, 0);
  return requestJson(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/diagnosis-runs?limit=${limit}&offset=${offset}`,
    { signal: options.signal },
    readMessages,
  );
}

export function fetchDiagnosisRun(incidentId, runId, options = {}) {
  return requestJson(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/diagnosis-runs/${encodeURIComponent(runId)}`,
    { signal: options.signal },
    readMessages,
  );
}

export function createDiagnosisRun(incidentId, evidenceRunId, idempotencyKey, options = {}) {
  return requestJson(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/diagnosis-runs`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ evidence_run_id: evidenceRunId }),
      signal: options.signal,
    },
    writeMessages,
  );
}
