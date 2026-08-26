import { describe, expect, it } from "vitest";

import { toAlertGroupDetail, toAlertGroupListItem, toAlertGroupSummary } from "./alertGroupView";

const group = {
  id: "agr_1", title: "支付接口错误率升高", state: "ACTIVE", storm_state: "STORM",
  severity: "high", service: "payment-api", environment: "production", symptom: "errors",
  active_count: 101, total_count: 101, impacted_resource_count: 101,
  first_observed_at: "2026-08-26T08:00:00Z", last_observed_at: "2026-08-26T08:01:00Z",
  explanation: "服务、环境和症状一致，已归入同一告警组。",
  incident: { id: "inc_1", title: "支付服务异常", state: "DETECTED", severity: "high" },
};

describe("告警组展示转换", () => {
  it("直接表达原始告警、影响资源、风暴和事故关系", () => {
    const view = toAlertGroupListItem(group);
    expect(view.memberText).toBe("101 条原始告警");
    expect(view.resourceText).toBe("影响 101 个资源");
    expect(view.stormText).toBe("告警风暴");
    expect(view.incidentText).toBe("已关联事故");
  });

  it("概况使用后端压缩率而不是当前成员页长度", () => {
    const view = toAlertGroupSummary({
      active_groups: 1, severe_active_groups: 1, active_alerts: 101, storm_groups: 1,
      resolved_groups: 0, compression_ratio: 101, peak_rate_per_minute: 101,
      pending_group_jobs: 0, calculated_at: "2026-08-26T08:02:00Z",
    });
    expect(view.compressionText).toBe("101:1");
    expect(view.peakText).toBe("101 条/分钟");
  });

  it("详情转换来源、级别和影响资源但不显示规则版本", () => {
    const detail = toAlertGroupDetail({
      group, reason_codes: ["same_service_environment_symptom_window"],
      source_distribution: [{ name: "生产 Alertmanager", count: 101 }],
      severity_distribution: [{ name: "high", count: 101 }],
      impacted_resources: [{ resource_type: "pod", resource_name: "payment-1", count: 1 }],
    });
    expect(detail.reason).toContain("服务、环境和症状一致");
    expect(detail.sources[0]).toEqual({ name: "生产 Alertmanager", count: 101 });
    expect(detail.severities[0].name).toBe("重要");
    expect(detail).not.toHaveProperty("ruleVersion");
  });
});
