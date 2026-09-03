import { mount } from "@vue/test-utils";
import { ref } from "vue";
import { expect, it, vi } from "vitest";

import IncidentEvidence from "./IncidentEvidence.vue";

vi.mock("../composables/useIncidentEvidence", () => ({ useIncidentEvidence: vi.fn() }));

it("默认展开第一条异常发现并收起正常证据", () => {
  const wrapper = mount(IncidentEvidence, { props: { incidentId: "inc_1", providedState: readyState() } });

  expect(wrapper.get('[data-testid="finding-attention"]').attributes("open")).toBeDefined();
  expect(wrapper.get('[data-testid="finding-normal"]').attributes("open")).toBeUndefined();
});

it("部分成功同时显示成功证据和缺失来源", () => {
  const wrapper = mount(IncidentEvidence, { props: { incidentId: "inc_1", providedState: readyState() } });

  expect(wrapper.text()).toContain("部分证据缺失");
  expect(wrapper.text()).toContain("Prometheus");
  expect(wrapper.text()).toContain("SkyWalking");
  expect(wrapper.text()).toContain("查询失败");
});

it("在原始证据前集中展示可复核的监控事实", () => {
  const wrapper = mount(IncidentEvidence, { props: { incidentId: "inc_1", providedState: readyState() } });

  const facts = wrapper.get('[data-testid="evidence-key-facts"]');
  expect(facts.text()).toContain("监控事实");
  expect(facts.text()).toContain("故障前服务可用性平均值为 1，故障期间为 0.8");
  expect(facts.text()).not.toContain("链路数据暂时不可用");
});

function readyState() {
  return {
    runs: ref([{ id: "evr_1", stateLabel: "部分证据缺失", createdAtLabel: "09/02 10:00:00" }]),
    selectedRunId: ref("evr_1"),
    detail: ref({
      run: {
        id: "evr_1", state: "PARTIAL", stateLabel: "部分证据缺失", triggerLabel: "Incident 自动取证",
        succeeded_count: 1, skipped_count: 0, missing_count: 0, failed_count: 1,
        createdAtLabel: "09/02 10:00:00", completedAtLabel: "09/02 10:01:00",
        window: { baseline_start: "2026-09-02T09:30:00Z", fault_end: "2026-09-02T10:00:00Z" },
        package_versions: { "common-service": 1 },
      },
      items: [
        { id: "a", display_name: "失败 Trace", source_type: "SKYWALKING", sourceLabel: "SkyWalking", state: "FAILED", stateLabel: "查询失败", needsAttention: true, tone: "attention", interpretation: "链路数据暂时不可用。", baseline_summary: {}, fault_summary: {} },
        { id: "b", display_name: "服务可用性", source_type: "PROMETHEUS", sourceLabel: "Prometheus", state: "SUCCEEDED", stateLabel: "已取得数据", needsAttention: false, tone: "normal", interpretation: "已获取指标趋势。", baseline_summary: { average: 1 }, fault_summary: { average: 0.8 } },
      ],
      keyFacts: [
        { id: "b", title: "服务可用性", sourceLabel: "Prometheus", text: "故障前服务可用性平均值为 1，故障期间为 0.8。" },
      ],
    }),
    state: ref("ready"), error: ref(""), mutationState: ref("idle"), mutationError: ref(""),
    load: vi.fn(), selectRun: vi.fn(), requestNewRun: vi.fn(),
  };
}
