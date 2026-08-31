import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createIncidentRule,
  deleteIncidentRule,
  disableIncidentRule,
  dryRunIncidentRule,
  fetchIncidentRules,
  publishIncidentRule,
  updateIncidentRule,
} from "./incidentRules";

const jsonResponse = (body, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(() => jsonResponse({ items: [], total: 0 })));
});

describe("Incident 规则 API", () => {
  it("列表只发送有值的筛选参数", async () => {
    await fetchIncidentRules({ state: "DRAFT", limit: 20, offset: 10 });
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/incident-rules?state=DRAFT&limit=20&offset=10",
      expect.objectContaining({ signal: undefined }),
    );
  });

  it("创建和更新携带幂等键与结构化规则", async () => {
    const command = { name: "支付异常", description: "", config: { window_minutes: 5 } };
    await createIncidentRule(command, "create-key");
    await updateIncidentRule("irl_1", { ...command, expected_version: 2 }, "update-key");

    expect(fetch).toHaveBeenNthCalledWith(
      1,
      "/api/v1/incident-rules",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": "create-key" }),
        body: JSON.stringify(command),
      }),
    );
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      "/api/v1/incident-rules/irl_1",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ ...command, expected_version: 2 }) }),
    );
  });

  it("把真实试运行范围和当前版本发送给后端", async () => {
    await dryRunIncidentRule("irl_1", { history_hours: 6, expected_version: 3 });
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/incident-rules/irl_1/dry-runs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ history_hours: 6, expected_version: 3 }),
      }),
    );
  });

  it("发布、停用和删除都发送当前版本", async () => {
    await publishIncidentRule("irl_1", 3, "publish-key");
    await disableIncidentRule("irl_1", 4, "disable-key");
    await deleteIncidentRule("irl_1", 3, "delete-key");
    expect(fetch.mock.calls.map((call) => [call[0], call[1].method, call[1].body])).toEqual([
      ["/api/v1/incident-rules/irl_1/publish", "POST", JSON.stringify({ expected_version: 3 })],
      ["/api/v1/incident-rules/irl_1/disable", "POST", JSON.stringify({ expected_version: 4 })],
      ["/api/v1/incident-rules/irl_1", "DELETE", JSON.stringify({ expected_version: 3 })],
    ]);
  });
});
