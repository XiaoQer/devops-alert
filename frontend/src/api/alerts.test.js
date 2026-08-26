import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchAlertOverview, fetchAlerts, fetchAlertSummary } from "./alerts";

afterEach(() => vi.unstubAllGlobals());

describe("告警中心 API", () => {
  it("只发送有效筛选且限制每页数量", async () => {
    const request = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [] }), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", request);
    await fetchAlerts({ state: "ACTIVE", environment: "", query: " 支付 ", limit: 500 });
    expect(request.mock.calls[0][0]).toBe("/api/v1/alerts?state=ACTIVE&query=%E6%94%AF%E4%BB%98&limit=100&offset=0");
  });

  it("读取概况和编码后的告警详情", async () => {
    const request = vi.fn().mockImplementation(() => Promise.resolve(new Response("{}", {
      status: 200, headers: { "Content-Type": "application/json" },
    })));
    vi.stubGlobal("fetch", request);
    await fetchAlertSummary("24h");
    await fetchAlertOverview("alt/1");
    expect(request.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/alerts/summary?window=24h", "/api/v1/alerts/alt%2F1/overview",
    ]);
  });
});
