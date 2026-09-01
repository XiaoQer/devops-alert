import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  acknowledgeIncident,
  fetchIncident,
  fetchIncidents,
  resolveIncident,
} from "./incidents";

const jsonResponse = (body, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(() => jsonResponse({ items: [], total: 0 })));
});

describe("Incident API", () => {
  it("列表支持多个状态和业务筛选", async () => {
    await fetchIncidents({
      states: ["OPEN", "ACKNOWLEDGED"], environment: "production", severity: "high",
      search: "checkout", limit: 20, offset: 10,
    });

    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/incidents?state=OPEN&state=ACKNOWLEDGED&environment=production&severity=high&search=checkout&limit=20&offset=10",
      expect.objectContaining({ signal: undefined }),
    );
  });

  it("读取详情时安全编码 Incident ID", async () => {
    await fetchIncident("inc_1/unsafe");
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/incidents/inc_1%2Funsafe",
      expect.objectContaining({ signal: undefined }),
    );
  });

  it("确认 Incident 时发送版本与幂等键", async () => {
    await acknowledgeIncident("inc_1", 1, "ack-key");
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/incidents/inc_1/acknowledge",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": "ack-key" }),
        body: JSON.stringify({ expected_version: 1 }),
      }),
    );
  });

  it("解决 Incident 时发送版本、解决说明与幂等键", async () => {
    await resolveIncident("inc_1", 2, "数据库连接已恢复", "resolve-key");
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/incidents/inc_1/resolve",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": "resolve-key" }),
        body: JSON.stringify({ expected_version: 2, resolution_summary: "数据库连接已恢复" }),
      }),
    );
  });
});
