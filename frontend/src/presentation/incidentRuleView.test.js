import { describe, expect, it } from "vitest";

import { createEmptyRuleDraft, toIncidentRuleListItem } from "./incidentRuleView";

describe("Incident 规则展示转换", () => {
  it("把后端状态转换为中文且保留结构化配置", () => {
    const item = toIncidentRuleListItem({
      id: "irl_1", name: "支付异常", state: "PUBLISHED", summary: "生产环境内按服务分组",
      config: { environment: "production", group_by: "SERVICE", window_minutes: 5, conditions: [] },
      publishable: false, version: 2, updated_at: "2026-08-31T10:00:00Z",
    });
    expect(item.stateLabel).toBe("已发布");
    expect(item.groupLabel).toBe("同一服务");
    expect(item.config.window_minutes).toBe(5);
  });

  it("新规则初始值不包含任何内置规则", () => {
    const draft = createEmptyRuleDraft();
    expect(draft.name).toBe("");
    expect(draft.config.environment).toBe("");
    expect(draft.config.conditions).toEqual([]);
  });
});
