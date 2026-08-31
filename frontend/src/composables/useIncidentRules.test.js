import { flushPromises } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createIncidentRule,
  dryRunIncidentRule,
  fetchIncidentRules,
  publishIncidentRule,
  updateIncidentRule,
} from "../api/incidentRules";
import { useIncidentRules } from "./useIncidentRules";

vi.mock("../api/incidentRules", () => ({
  fetchIncidentRules: vi.fn(), fetchIncidentRule: vi.fn(), createIncidentRule: vi.fn(),
  updateIncidentRule: vi.fn(), deleteIncidentRule: vi.fn(), dryRunIncidentRule: vi.fn(),
  publishIncidentRule: vi.fn(), disableIncidentRule: vi.fn(), copyIncidentRule: vi.fn(),
}));

const rule = {
  id: "irl_1", name: "支付异常", description: "", state: "DRAFT", summary: "生产环境内",
  config: { environment: "production", alert_source_ids: [], services: [], group_by: "SERVICE", window_minutes: 5, conditions: [{ type: "ACTIVE_ALERTS_GTE", threshold: 2 }] },
  publishable: false, version: 1, updated_at: "2026-08-31T10:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  fetchIncidentRules.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
});

describe("Incident 规则页面状态", () => {
  it("真实空列表进入空状态，不创建演示规则", async () => {
    const state = useIncidentRules({ autoLoad: false });
    await state.loadRules();
    expect(state.listState.value).toBe("empty");
    expect(state.rules.value).toEqual([]);
    expect(createIncidentRule).not.toHaveBeenCalled();
  });

  it("保存草稿成功后使用后端规则版本", async () => {
    createIncidentRule.mockResolvedValue({ rule, replayed: false });
    const state = useIncidentRules({ autoLoad: false });
    state.startCreate();
    Object.assign(state.draft.value, { name: "支付异常", description: "" });
    Object.assign(state.draft.value.config, rule.config);

    expect(await state.saveDraft()).toBe(true);
    expect(state.currentRule.value.version).toBe(1);
    expect(state.operationState.value).toBe("succeeded");
  });

  it("字段改变后清除旧试运行并通过后端 publishable 控制发布", async () => {
    dryRunIncidentRule.mockResolvedValue({
      id: "ird_1", rule: { ...rule, publishable: true }, scanned_alert_count: 48,
      match_count: 2, truncated: false, matches: [],
    });
    publishIncidentRule.mockResolvedValue({ rule: { ...rule, state: "PUBLISHED", version: 2 }, replayed: false });
    const state = useIncidentRules({ autoLoad: false });
    state.openRule(rule);
    await state.runDryRun(6);
    expect(state.currentRule.value.publishable).toBe(true);
    expect(state.dryRunResult.value.match_count).toBe(2);

    state.updateDraft({ name: "支付异常新版" });
    expect(state.dryRunResult.value).toBeNull();
    updateIncidentRule.mockResolvedValue({ rule: { ...rule, name: "支付异常新版", version: 2 }, replayed: false });
    await state.saveDraft();
    expect(state.currentRule.value.publishable).toBe(false);
  });

  it("冲突时保留用户输入并提示重新加载", async () => {
    updateIncidentRule.mockRejectedValue({ code: "incident_rule_version_conflict", userMessage: "规则已被更新" });
    const state = useIncidentRules({ autoLoad: false });
    state.openRule(rule);
    state.updateDraft({ name: "我的修改" });
    expect(await state.saveDraft()).toBe(false);
    expect(state.draft.value.name).toBe("我的修改");
    expect(state.operationState.value).toBe("conflict");
  });
});
