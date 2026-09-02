import { expect, it } from "vitest";

import { toEvidenceItemView, toEvidenceRunView } from "./evidenceView";

it("把部分成功和无数据区分成中文状态", () => {
  expect(toEvidenceRunView({ state: "PARTIAL" }).stateLabel).toBe("部分证据缺失");
  expect(toEvidenceItemView({ state: "NO_DATA", source_type: "PROMETHEUS" }).stateLabel).toBe("未发现数据");
});

it("缺失和失败证据默认标记为需要关注", () => {
  expect(toEvidenceItemView({ state: "FAILED", source_type: "SKYWALKING" }).needsAttention).toBe(true);
  expect(toEvidenceItemView({ state: "SUCCEEDED", source_type: "PROMETHEUS" }).needsAttention).toBe(false);
});
