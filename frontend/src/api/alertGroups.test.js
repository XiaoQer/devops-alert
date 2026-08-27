import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchAlertGroupMembers, fetchAlertGroups, fetchAlertGroupSummary, fetchIncidentAlertGroups, splitAlertGroupMembers } from "./alertGroups";

afterEach(() => vi.unstubAllGlobals());

describe("告警组 API", () => {
  it("列表传递筛选和独立分页", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], total: 0, limit: 50, offset: 50 }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await fetchAlertGroups({ view: "current", state: "ACTIVE", storm_state: "STORM", query: "支付", limit: 50, offset: 50 });
    expect(fetchMock.mock.calls[0][0]).toContain("view=current");
    expect(fetchMock.mock.calls[0][0]).toContain("state=ACTIVE");
    expect(fetchMock.mock.calls[0][0]).toContain("storm_state=STORM");
    expect(fetchMock.mock.calls[0][0]).toContain("offset=50");
  });

  it("人工拆分携带版本、原因和幂等键", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ operation_id: "aeo_1" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await splitAlertGroupMembers("agr_1", { expected_version: 3, alert_ids: ["alt_1"], reason: "独立故障" }, "split-1");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/alert-groups/agr_1/members/split");
    expect(fetchMock.mock.calls[0][1].headers["Idempotency-Key"]).toBe("split-1");
    expect(fetchMock.mock.calls[0][1].body).toContain('"expected_version":3');
  });

  it("成员第二页和概况使用独立地址", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ items: [], total: 101, limit: 100, offset: 100 }), { status: 200, headers: { "Content-Type": "application/json" } })));
    vi.stubGlobal("fetch", fetchMock);
    await fetchAlertGroupMembers("agr_1", { limit: 100, offset: 100 });
    await fetchAlertGroupSummary("24h");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/alert-groups/agr_1/alerts?limit=100&offset=100");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/alert-groups/summary?window=24h");
  });

  it("事故读取关联告警组而不是只读取原始告警", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await fetchIncidentAlertGroups("inc_1", { limit: 50, offset: 0 });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/incidents/inc_1/alert-groups?limit=50&offset=0");
  });
});
