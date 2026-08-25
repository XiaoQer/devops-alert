import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import App from "./App.vue";

describe("事故中心", () => {
  it("默认展示事故队列、中文关联原因和三条关联告警", () => {
    const wrapper = mount(App);

    expect(wrapper.get("h1").text()).toBe("事故中心");
    expect(wrapper.text()).toContain("支付接口错误率持续升高");
    expect(wrapper.text()).toContain(
      "3 条告警来自同一生产服务，均发生在 15 分钟窗口内，因此自动关联。",
    );
    expect(wrapper.findAll('[data-testid="related-alert-row"]')).toHaveLength(3);
    expect(wrapper.text()).not.toContain("correlation.v1");
  });

  it("切换事故后同步更新工作区", async () => {
    const wrapper = mount(App);

    await wrapper.get('[data-testid="incident-inc-order"] button').trigger("click");

    expect(wrapper.get('[data-testid="incident-title"]').text()).toContain(
      "订单服务响应变慢",
    );
    expect(wrapper.text()).toContain("订单请求延迟升高，部分用户提交订单变慢");
    expect(wrapper.findAll('[data-testid="related-alert-row"]')).toHaveLength(2);
  });

  it("可以搜索事故并切换环境", async () => {
    const wrapper = mount(App);

    await wrapper.get('[aria-label="搜索事故、服务或团队"]').setValue("用户中心");
    expect(wrapper.findAll('[data-testid^="incident-inc-"]')).toHaveLength(1);
    expect(wrapper.text()).toContain("用户中心 Pod 反复重启");

    await wrapper.get('[aria-label="环境筛选"]').setValue("staging");
    expect(wrapper.text()).toContain("没有符合条件的事故");
  });

  it("认领事故后更新按钮与负责人", async () => {
    const wrapper = mount(App);

    await wrapper.get('[data-testid="claim-incident"]').trigger("click");

    expect(wrapper.get('[data-testid="claim-incident"]').text()).toContain("已认领");
    expect(wrapper.get('[data-testid="incident-owner"]').text()).toContain("张工程师");
  });

  it("技术详情默认收起且可以展开", async () => {
    const wrapper = mount(App);

    expect(wrapper.find('[data-testid="technical-content"]').exists()).toBe(false);
    await wrapper.get('[data-testid="technical-toggle"]').trigger("click");

    expect(wrapper.get('[data-testid="technical-content"]').text()).toContain(
      "correlation.v1",
    );
  });
});
