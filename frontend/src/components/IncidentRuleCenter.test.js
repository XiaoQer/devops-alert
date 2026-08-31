import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";

import { fetchAlertSources } from "../api/alertSources";
import { fetchIncidentRules } from "../api/incidentRules";
import IncidentRuleCenter from "./IncidentRuleCenter.vue";

vi.mock("../api/incidentRules", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchIncidentRules: vi.fn(),
}));
vi.mock("../api/alertSources", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchAlertSources: vi.fn(),
}));

beforeEach(() => {
  fetchIncidentRules.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  fetchAlertSources.mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
});

it("没有规则时只显示真实空状态", async () => {
  const wrapper = mount(IncidentRuleCenter);
  await flushPromises();
  expect(wrapper.text()).toContain("还没有 Incident 规则");
  expect(wrapper.text()).not.toContain("示例规则");
  expect(wrapper.findAll("[data-testid='rule-list-item']")).toHaveLength(0);
});

it("从空状态进入四步创建流程", async () => {
  const wrapper = mount(IncidentRuleCenter);
  await flushPromises();
  await wrapper.get("[data-testid='create-incident-rule']").trigger("click");
  expect(wrapper.get("[data-testid='incident-rule-wizard']").exists()).toBe(true);
  expect(wrapper.text()).toContain("基本信息");
  expect(wrapper.text()).toContain("匹配范围");
  expect(wrapper.text()).toContain("触发条件");
  expect(wrapper.text()).toContain("试运行与发布");
});
