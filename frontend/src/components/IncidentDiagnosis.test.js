import { mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";

import IncidentDiagnosis from "./IncidentDiagnosis.vue";

it("仅允许基于完成的取证手动启动本地诊断演示", async () => {
  const requestRun = vi.fn();
  const wrapper = mount(IncidentDiagnosis, {
    props: {
      incidentId: "inc_1",
      evidenceRuns: [
        { id: "evr_queued", state: "QUEUED", createdAtLabel: "09/15 10:00:00" },
        { id: "evr_done", state: "PARTIAL", createdAtLabel: "09/15 10:01:00" },
        { id: "evr_done_2", state: "SUCCEEDED", createdAtLabel: "09/15 10:02:00" },
      ],
      diagnosis: { state: "empty", runs: [], detail: null, error: "", mutationState: "idle", mutationError: "" },
      onRequestRun: requestRun,
    },
  });

  expect(wrapper.text()).toContain("本地 Dify 仿真");
  expect(wrapper.text()).toContain("incident-diagnosis.v1");
  expect(wrapper.text()).toContain("Dify Workflow");
  expect(wrapper.text()).toContain("平台校验器");
  expect(wrapper.findAll("option")).toHaveLength(2);
  await wrapper.get('select[aria-label="诊断取证运行"]').setValue("evr_done_2");
  await wrapper.get('[data-testid="start-diagnosis"]').trigger("click");
  expect(requestRun).toHaveBeenCalledWith("evr_done_2");
});
