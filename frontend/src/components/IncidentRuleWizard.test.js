import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";

import IncidentRuleWizard from "./IncidentRuleWizard.vue";

const validDraft = {
  name: "支付异常",
  description: "识别支付服务异常",
  config: {
    environment: "production", alert_source_ids: [], services: ["checkout"],
    group_by: "SERVICE", window_minutes: 5,
    conditions: [
      { type: "DISTINCT_ALERT_NAMES_GTE", threshold: 2 },
      { type: "MAX_SEVERITY_AT_LEAST", severity: "high" },
    ],
  },
};

const mountWizard = (props = {}) => mount(IncidentRuleWizard, {
  props: {
    draft: structuredClone(validDraft), currentRule: null, step: 3,
    dryRunResult: null, operationState: "idle", operationError: "", sources: [],
    ...props,
  },
});

it("第三步集中展示窗口、条件和中文摘要", () => {
  const wrapper = mountWizard();
  expect(wrapper.get("[data-testid='incident-rule-wizard']").text()).toContain("5 分钟");
  expect(wrapper.text()).toContain("不同 Alertname 数量不少于 2");
  expect(wrapper.text()).toContain("最高告警级别至少为高");
  expect(wrapper.text()).toContain("下一步：试运行");
});

it("允许直接选择条件类型并阻止重复类型", async () => {
  const wrapper = mountWizard();
  const typeSelectors = wrapper.findAll("[data-testid='condition-type']");
  expect(typeSelectors).toHaveLength(2);
  expect(typeSelectors[0].findAll("option").map((option) => option.attributes("disabled"))).toEqual([
    undefined, undefined, undefined, "",
  ]);

  await typeSelectors[1].setValue("ACTIVE_ALERTS_GTE");

  expect(wrapper.emitted("config-change").at(-1)[0]).toEqual({
    conditions: [
      { type: "DISTINCT_ALERT_NAMES_GTE", threshold: 2 },
      { type: "ACTIVE_ALERTS_GTE", threshold: 2 },
    ],
  });
});

it("没有后端成功试运行时禁止发布", () => {
  const wrapper = mountWizard({ step: 4, currentRule: { id: "irl_1", version: 1, state: "DRAFT", publishable: false } });
  expect(wrapper.get("[data-testid='publish-rule']").attributes("disabled")).toBeDefined();
  expect(wrapper.text()).toContain("发布规则不会在本阶段自动创建 Incident");
});

it("展示真实试运行统计并允许发布", () => {
  const wrapper = mountWizard({
    step: 4,
    currentRule: { id: "irl_1", version: 1, state: "DRAFT", publishable: true },
    dryRunResult: { scanned_alert_count: 48, match_count: 2, truncated: false, matches: [] },
  });
  expect(wrapper.text()).toContain("扫描 48 条告警，命中 2 个窗口");
  expect(wrapper.get("[data-testid='publish-rule']").attributes("disabled")).toBeUndefined();
});
