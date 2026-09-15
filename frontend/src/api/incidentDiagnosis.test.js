import { afterEach, expect, it, vi } from "vitest";

import { createDiagnosisRun, fetchDiagnosisRuns } from "./incidentDiagnosis";

afterEach(() => vi.unstubAllGlobals());

it("创建诊断时只提交取证运行和幂等键", async () => {
  const request = vi.fn().mockResolvedValue(new Response(JSON.stringify({ run: {} }), {
    status: 200, headers: { "Content-Type": "application/json" },
  }));
  vi.stubGlobal("fetch", request);

  await createDiagnosisRun("inc_1", "evr_1", "key-1");

  expect(request.mock.calls[0][0]).toContain("/inc_1/diagnosis-runs");
  expect(request.mock.calls[0][1]).toEqual(expect.objectContaining({
    method: "POST",
    headers: expect.objectContaining({ "Idempotency-Key": "key-1" }),
    body: JSON.stringify({ evidence_run_id: "evr_1" }),
  }));
});

it("诊断历史读取最多五十条", async () => {
  const request = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], total: 0 }), {
    status: 200, headers: { "Content-Type": "application/json" },
  }));
  vi.stubGlobal("fetch", request);

  await fetchDiagnosisRuns("inc_1", { limit: 500 });

  expect(request.mock.calls[0][0]).toContain("limit=50");
});
