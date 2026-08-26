import { afterEach, describe, expect, it, vi } from "vitest";

import { createAlertSource, fetchAlertSource, fetchAlertSourceReceipts, fetchAlertSources, revokeAlertSourceCredential, rotateAlertSourceCredential, updateAlertSource } from "./alertSources";

afterEach(() => vi.unstubAllGlobals());
const ok = () => Promise.resolve(new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));

describe("告警源管理 API", () => {
  it("区分读取不可用与写操作结果未知", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("connection refused")));
    await expect(fetchAlertSources()).rejects.toMatchObject({
      userMessage: "告警源数据暂时不可用，请稍后重试",
    });
    await expect(createAlertSource({ name: "生产来源", source_type: "ALERTMANAGER" }, "key"))
      .rejects.toMatchObject({ userMessage: "告警源服务暂时不可用，本次操作结果未知" });
  });

  it("读接口使用有界参数", async () => {
    const request = vi.fn().mockImplementation(ok); vi.stubGlobal("fetch", request);
    await fetchAlertSources({ source_type: "ALERTMANAGER", limit: 200 });
    await fetchAlertSource("src/1"); await fetchAlertSourceReceipts("src/1", { limit: 200 });
    expect(request.mock.calls.map((call) => call[0])).toEqual(["/api/v1/alert-sources?source_type=ALERTMANAGER&limit=100&offset=0", "/api/v1/alert-sources/src%2F1", "/api/v1/alert-sources/src%2F1/receipts?limit=100&offset=0"]);
  });

  it("所有写操作发送调用方生成的幂等键和版本", async () => {
    const request = vi.fn().mockImplementation(ok); vi.stubGlobal("fetch", request);
    await createAlertSource({ name: "生产来源", source_type: "ALERTMANAGER" }, "same-key");
    await updateAlertSource("src_1", { expected_version: 1, state: "DISABLED" }, "same-key");
    await rotateAlertSourceCredential("src_1", 2, "same-key");
    await revokeAlertSourceCredential("src_1", "acr_1", 3, "same-key");
    expect(request.mock.calls.every((call) => call[1].headers["Idempotency-Key"] === "same-key")).toBe(true);
    expect(request.mock.calls.map((call) => call[0])).toEqual(["/api/v1/alert-sources", "/api/v1/alert-sources/src_1", "/api/v1/alert-sources/src_1/credentials/rotate", "/api/v1/alert-sources/src_1/credentials/acr_1/revoke"]);
  });
});
