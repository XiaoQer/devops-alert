import { afterEach, expect, it, vi } from "vitest";

import { fetchEvidenceRuns, requestEvidenceRun } from "./incidentEvidence";

afterEach(() => vi.unstubAllGlobals());

it("重新取证发送幂等键且不提交查询内容", async () => {
  const request = vi.fn().mockResolvedValue(new Response(JSON.stringify({ run: {}, replayed: false }), {
    status: 200, headers: { "Content-Type": "application/json" },
  }));
  vi.stubGlobal("fetch", request);

  await requestEvidenceRun("inc_1", "key-1");

  expect(request.mock.calls[0][0]).toContain("/inc_1/evidence-runs");
  expect(request.mock.calls[0][1]).toEqual(expect.objectContaining({
    method: "POST", headers: expect.objectContaining({ "Idempotency-Key": "key-1" }),
  }));
  expect(request.mock.calls[0][1].body).toBeUndefined();
});

it("历史列表限制在五十条以内", async () => {
  const request = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], total: 0 }), {
    status: 200, headers: { "Content-Type": "application/json" },
  }));
  vi.stubGlobal("fetch", request);

  await fetchEvidenceRuns("inc_1", { limit: 500 });

  expect(request.mock.calls[0][0]).toContain("limit=50");
});
