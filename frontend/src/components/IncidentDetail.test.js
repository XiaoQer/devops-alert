import { mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";

import IncidentDetail from "./IncidentDetail.vue";

const incident = { id: "inc_1", reference: "INC-20260901-001", title: "checkout异常", state: "OPEN", stateLabel: "待确认", severityLabel: "严重", environmentLabel: "生产环境", group_display_name: "checkout", alertSummary: "2 条活跃 / 共 3 条", rule_name: "支付异常", rule_summary: "5 分钟内 2 条", openedAtLabel: "09/01 09:00:00", updatedAtLabel: "09/01 09:10:00", version: 1 };

it("确认与解决动作保持简单且解决必须填写说明", async () => {
  const wrapper = mount(IncidentDetail, { props: { detail: { incident, alerts: [], activities: [], feishu: { statusLabel: "未配置飞书事故群" } }, operationState: "idle", operationError: "", resolutionSummary: "" } });
  await wrapper.get('[data-testid="acknowledge-incident"]').trigger("click");
  expect(wrapper.emitted("acknowledge")).toHaveLength(1);
  await wrapper.get('[data-testid="open-resolve"]').trigger("click");
  expect(wrapper.get('[data-testid="resolution-summary"]').exists()).toBe(true);
  await wrapper.get('[data-testid="resolution-summary"]').setValue("数据库连接已恢复");
  expect(wrapper.emitted("update:resolutionSummary")[0]).toEqual(["数据库连接已恢复"]);
});

it("关联告警为空时明确展示真实空态", () => {
  const wrapper = mount(IncidentDetail, { props: { detail: { incident, alerts: [], activities: [], feishu: { statusLabel: "未配置飞书事故群" } }, operationState: "idle", operationError: "", resolutionSummary: "" } });
  expect(wrapper.text()).toContain("当前没有可展示的关联告警");
  expect(wrapper.text()).not.toContain("示例");
});
