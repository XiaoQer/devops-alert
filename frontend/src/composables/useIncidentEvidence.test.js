import { ref } from "vue";
import { beforeEach, expect, it, vi } from "vitest";

import { fetchEvidenceRun, fetchEvidenceRuns, requestEvidenceRun } from "../api/incidentEvidence";
import { useIncidentEvidence } from "./useIncidentEvidence";

vi.mock("../api/incidentEvidence", () => ({
  fetchEvidenceRun: vi.fn(), fetchEvidenceRuns: vi.fn(), requestEvidenceRun: vi.fn(),
}));

const run = { id: "evr_1", state: "PARTIAL", trigger: "AUTOMATIC", created_at: "2026-09-02T10:00:00Z" };

beforeEach(() => {
  vi.clearAllMocks();
  fetchEvidenceRuns.mockResolvedValue({ items: [run], total: 1 });
  fetchEvidenceRun.mockResolvedValue({ run, items: [], items_truncated: false });
});

it("默认选择最新一次运行并读取详情", async () => {
  const state = useIncidentEvidence(ref("inc_1"), { autoLoad: false });

  await state.load();

  expect(state.state.value).toBe("ready");
  expect(state.selectedRunId.value).toBe("evr_1");
  expect(fetchEvidenceRun).toHaveBeenCalledWith("inc_1", "evr_1", expect.any(Object));
});

it("人工重新取证后切换到新运行", async () => {
  requestEvidenceRun.mockResolvedValue({ run: { ...run, id: "evr_2", state: "QUEUED" }, replayed: false });
  fetchEvidenceRun.mockResolvedValueOnce({ run: { ...run, id: "evr_2", state: "QUEUED" }, items: [] });
  const state = useIncidentEvidence(ref("inc_1"), { autoLoad: false });

  await state.requestNewRun();

  expect(state.selectedRunId.value).toBe("evr_2");
  expect(state.mutationState.value).toBe("succeeded");
});
