import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createIncidentNotificationRoute,
  fetchIncidentNotificationRoutes,
  updateIncidentNotificationRoute,
} from "./incidentNotificationRoutes";

const jsonResponse = (body, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(() => jsonResponse({ items: [], total: 0 })));
});

describe("Incident 飞书通知路由 API", () => {
  it("读取飞书通知路由", async () => {
    await fetchIncidentNotificationRoutes();
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/incident-notification-routes",
      expect.objectContaining({ signal: undefined }),
    );
  });

  it("创建通知路由时发送幂等键", async () => {
    const command = { environment: "production", chat_id: "oc_123", chat_name: "生产事故群", enabled: true };
    await createIncidentNotificationRoute(command, "create-key");
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/incident-notification-routes",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": "create-key" }),
        body: JSON.stringify(command),
      }),
    );
  });

  it("更新通知路由时携带当前版本", async () => {
    const command = { environment: "staging", chat_id: "oc_456", chat_name: "预发事故群", enabled: false };
    await updateIncidentNotificationRoute("inr_1", command, 3, "update-key");
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/incident-notification-routes/inr_1",
      expect.objectContaining({
        method: "PATCH",
        headers: expect.objectContaining({ "Idempotency-Key": "update-key" }),
        body: JSON.stringify({ ...command, expected_version: 3 }),
      }),
    );
  });
});
