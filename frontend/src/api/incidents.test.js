import { afterEach, describe, expect, it, vi } from "vitest";

import {
  executeIncidentAction,
  fetchIncidentOverview,
  fetchIncidents,
} from "./incidents";

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

  it("读取详情使用相对地址", async () => {
    const response = () =>
      new Response(JSON.stringify({ id: "inc_1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    const request = vi.fn().mockResolvedValueOnce(response());
    vi.stubGlobal("fetch", request);

    await fetchIncidentOverview("inc_1");

    expect(request.mock.calls[0][0]).toBe("/api/v1/incidents/inc_1/overview");
  });

  it.each(["claim", "release", "transitions", "notes", "resolve", "reopen", "close"])(
    "%s 通过同源接口发送版本和幂等键",
    async (action) => {
      const request = vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ version: 2 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
      vi.stubGlobal("fetch", request);

      await executeIncidentAction(
        "inc_1",
        action,
        { expected_version: 1 },
        "operation-key",
      );

      expect(request.mock.calls[0][0]).toBe(`/api/v1/incidents/inc_1/${action}`);
      expect(request.mock.calls[0][1].headers).toMatchObject({
        Accept: "application/json",
        "Content-Type": "application/json",
        "Idempotency-Key": "operation-key",
      });
      expect(request.mock.calls[0][1].headers.Authorization).toBeUndefined();
      expect(request.mock.calls[0][1].body).toBe('{"expected_version":1}');
    },
  );

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

  it("代理无正文 5xx 视为结果未知并允许安全重试", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 500 })),
    );

    await expect(executeIncidentAction(
      "inc_1",
      "notes",
      { expected_version: 1, category: "GENERAL", message: "待保存记录" },
      "stable-operation-key",
    )).rejects.toMatchObject({
      code: "incident_api_unavailable",
      userMessage: "事故服务暂时不可用，本次操作结果未知",
    });
  });
});
