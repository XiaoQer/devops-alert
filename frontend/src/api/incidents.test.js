import { afterEach, describe, expect, it, vi } from "vitest";

import { claimIncident, fetchIncidentOverview, fetchIncidents } from "./incidents";

afterEach(() => vi.unstubAllGlobals());

describe("事故中心 API 客户端", () => {
  it("只请求同源列表接口并编码筛选条件", async () => {
    const request = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0, limit: 20, offset: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", request);

    await fetchIncidents({ environment: "production", query: "支付 api" });

    expect(request.mock.calls[0][0]).toBe(
      "/api/v1/incidents?environment=production&query=%E6%94%AF%E4%BB%98+api&limit=100&offset=0",
    );
    expect(request.mock.calls[0][1].headers).toEqual({ Accept: "application/json" });
  });

  it("读取详情和认领都使用相对地址", async () => {
    const response = () =>
      new Response(JSON.stringify({ id: "inc_1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    const request = vi.fn()
      .mockResolvedValueOnce(response())
      .mockResolvedValueOnce(response());
    vi.stubGlobal("fetch", request);

    await fetchIncidentOverview("inc_1");
    await claimIncident("inc_1");

    expect(request.mock.calls[0][0]).toBe("/api/v1/incidents/inc_1/overview");
    expect(request.mock.calls[1][0]).toBe("/api/v1/incidents/inc_1/claim");
    expect(request.mock.calls[1][1].method).toBe("POST");
  });

  it("把后端安全错误转换为用户可读异常", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ code: "persistence_unavailable", message: "事故记录暂时不可用" }),
          { status: 503, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(fetchIncidents({})).rejects.toMatchObject({
      code: "persistence_unavailable",
      userMessage: "事故记录暂时不可用",
    });
  });

  it("网络异常不暴露底层错误", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED 127.0.0.1")));

    await expect(fetchIncidents({})).rejects.toMatchObject({
      code: "incident_api_unavailable",
      userMessage: "事故数据暂时不可用，请稍后重试",
    });
  });
});
