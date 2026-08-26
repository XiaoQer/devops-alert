import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createAlertSource, fetchAlertSource, fetchAlertSourceReceipts, fetchAlertSources } from "../api/alertSources";
import { ApiError } from "../api/request";
import { useAlertSources } from "./useAlertSources";

vi.mock("../api/alertSources", () => ({ createAlertSource: vi.fn(), fetchAlertSource: vi.fn(), fetchAlertSourceReceipts: vi.fn(), fetchAlertSources: vi.fn(), updateAlertSource: vi.fn(), rotateAlertSourceCredential: vi.fn(), revokeAlertSourceCredential: vi.fn() }));
const source = { id: "src_1", name: "生产来源", source_type: "ALERTMANAGER", management_type: "USER_MANAGED", state: "ENABLED", version: 1, last_accepted_at: null, last_rejected_at: null, last_validated_at: null, accepted_requests: 0, rejected_requests: 0, opened_count: 0, updated_count: 0, resolved_count: 0, replayed_count: 0, ignored_count: 0, created_at: "2026-08-26T08:00:00Z", updated_at: "2026-08-26T08:00:00Z", credentials: [] };
function mountState() { let state; const wrapper = mount(defineComponent({ setup() { state = useAlertSources(); return () => h("div"); } })); return { wrapper, get state() { return state; } }; }
beforeEach(() => { fetchAlertSources.mockResolvedValue({ items: [source], total: 1, limit: 50, offset: 0 }); fetchAlertSource.mockResolvedValue(source); fetchAlertSourceReceipts.mockResolvedValue({ items: [], total: 0 }); });
afterEach(() => vi.clearAllMocks());

describe("告警源安全操作状态", () => {
  it("网络结果未知时保留原幂等键且不自动生成第二个 Token", async () => {
    createAlertSource.mockRejectedValueOnce(new ApiError("api_unavailable", "结果未知")).mockResolvedValueOnce({ source, token: null });
    const mounted = mountState(); await flushPromises();
    await mounted.state.submitCreate({ name: "生产来源", source_type: "ALERTMANAGER" });
    const retryKey = mounted.state.retryableOperation.value.idempotencyKey;
    await mounted.state.retryLastOperation();
    expect(createAlertSource.mock.calls[1][1]).toBe(retryKey);
    mounted.wrapper.unmount();
  });

  it("一次性 Token 确认后从状态中清除", async () => {
    const token = ["iisrc", "acr_test.secret"].join("_");
    createAlertSource.mockResolvedValueOnce({ source, token, secret_retrievable: true });
    const mounted = mountState(); await flushPromises(); await mounted.state.submitCreate({ name: "新来源", source_type: "ALERTMANAGER" });
    expect(mounted.state.oneTimeToken.value).toBe(token);
    mounted.state.clearOneTimeToken();
    expect(mounted.state.oneTimeToken.value).toBeNull();
    mounted.wrapper.unmount();
  });
});
