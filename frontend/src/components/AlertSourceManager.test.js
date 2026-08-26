import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import { createAlertSource, fetchAlertSource, fetchAlertSourceReceipts, fetchAlertSources } from "../api/alertSources";
import AlertSourceManager from "./AlertSourceManager.vue";

vi.mock("../api/alertSources", () => ({ createAlertSource: vi.fn(), fetchAlertSource: vi.fn(), fetchAlertSourceReceipts: vi.fn(), fetchAlertSources: vi.fn(), updateAlertSource: vi.fn(), rotateAlertSourceCredential: vi.fn(), revokeAlertSourceCredential: vi.fn() }));

describe("告警源管理页面", () => {
  it("展示真实来源并在关闭确认后清除一次性 Token", async () => {
    const source = { id: "src_1", name: "生产 Prometheus", source_type: "ALERTMANAGER", management_type: "USER_MANAGED", state: "ENABLED", version: 1, last_accepted_at: null, last_rejected_at: null, last_validated_at: null, accepted_requests: 0, rejected_requests: 0, opened_count: 0, updated_count: 0, resolved_count: 0, replayed_count: 0, ignored_count: 0, created_at: "2026-08-26T08:00:00Z", updated_at: "2026-08-26T08:00:00Z", credentials: [] };
    const token = ["iisrc", "acr_test.secret"].join("_");
    fetchAlertSources.mockResolvedValue({ items: [source], total: 1 }); fetchAlertSource.mockResolvedValue(source); fetchAlertSourceReceipts.mockResolvedValue({ items: [], total: 0 }); createAlertSource.mockResolvedValue({ source, token, secret_retrievable: true });
    const storageSpy = vi.spyOn(Storage.prototype, "setItem");
    const wrapper = mount(AlertSourceManager); await flushPromises();
    expect(wrapper.text()).toContain("生产 Prometheus");
    await wrapper.get('[data-testid="create-source"]').trigger("click");
    await wrapper.get('[data-testid="source-name"]').setValue("生产来源");
    await wrapper.get('[data-testid="submit-source"]').trigger("click"); await flushPromises();
    expect(wrapper.text()).toContain(token);
    await wrapper.get('[data-testid="confirm-token-saved"]').trigger("click");
    expect(wrapper.text()).not.toContain(token);
    expect(storageSpy).not.toHaveBeenCalled();
    storageSpy.mockRestore();
  });
});
