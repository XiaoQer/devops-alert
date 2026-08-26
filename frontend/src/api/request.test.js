import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, requestJson } from "./request";

afterEach(() => vi.unstubAllGlobals());

describe("共享 API 请求边界", () => {
  it("保留后端安全错误码和中文消息", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ code: "alert_source_disabled", message: "告警源已停用" }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    await expect(requestJson("/api/v1/example")).rejects.toMatchObject({
      code: "alert_source_disabled",
      userMessage: "告警源已停用",
      status: 409,
    });
  });

  it("把非结构化 5xx 转为页面指定的服务不可用消息", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response("bad gateway", { status: 502, headers: { "Content-Type": "text/plain" } }),
    ));

    await expect(requestJson(
      "/api/v1/alerts",
      {},
      { unavailable: "告警服务暂时不可用" },
    )).rejects.toMatchObject({
      code: "api_unavailable",
      userMessage: "告警服务暂时不可用",
      status: 502,
    });
  });

  it("合并调用方请求头且不吞掉取消信号", async () => {
    const request = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", request);
    await requestJson("/api/v1/example", { headers: { "Idempotency-Key": "key-1" } });
    expect(request.mock.calls[0][1].headers).toEqual({
      Accept: "application/json",
      "Idempotency-Key": "key-1",
    });

    const aborted = new DOMException("aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(aborted));
    await expect(requestJson("/api/v1/example")).rejects.toBe(aborted);
  });

  it("网络异常只返回安全错误", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED secret-host")));
    await expect(requestJson("/api/v1/example")).rejects.toBeInstanceOf(ApiError);
    await expect(requestJson("/api/v1/example")).rejects.toMatchObject({
      code: "api_unavailable",
      userMessage: "服务暂时不可用，请稍后重试",
    });
  });
});
