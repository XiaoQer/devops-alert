import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";

import { fetchIncidentNotificationRoutes } from "../api/incidentNotificationRoutes";
import IncidentNotificationSettings from "./IncidentNotificationSettings.vue";

vi.mock("../api/incidentNotificationRoutes", () => ({ fetchIncidentNotificationRoutes: vi.fn(), createIncidentNotificationRoute: vi.fn(), updateIncidentNotificationRoute: vi.fn() }));

beforeEach(() => vi.clearAllMocks());

it("只展示凭据完整性和安全缩略的群标识", async () => {
  fetchIncidentNotificationRoutes.mockResolvedValue({ items: [{ id: "inr_1", environment: "production", chat_id: "oc_1234567890", chat_name: "生产事故群", enabled: true, version: 1, updated_at: "2026-09-01T01:00:00Z" }], total: 1, feishu_capability: { configured: true, missing_environment_keys: [] } });
  const wrapper = mount(IncidentNotificationSettings);
  await flushPromises();
  expect(wrapper.text()).toContain("oc_1…7890");
  expect(wrapper.text()).not.toContain("oc_1234567890");
  expect(wrapper.find('input[type="password"]').exists()).toBe(false);
});
