import { describe, expect, it } from "vitest";

import {
  toIncidentActivityView,
  toIncidentDetailView,
  toIncidentListItem,
  toNotificationRouteView,
} from "./incidentView";

const incident = {
  id: "inc_1", reference: "INC-20260901-001", title: "production checkout异常",
  state: "OPEN", severity: "critical", environment: "production", group_by: "SERVICE",
  group_key: "checkout", group_display_name: "checkout", incident_rule_id: "irl_1",
  incident_rule_version: 2, alert_count: 5, active_alert_count: 3,
  distinct_alert_name_count: 2, version: 4, opened_at: "2026-09-01T01:00:00Z",
  acknowledged_at: null, resolved_at: null, resolution_summary: null,
  last_alert_at: "2026-09-01T01:10:00Z", created_at: "2026-09-01T01:00:00Z",
  updated_at: "2026-09-01T01:10:00Z", rule_name: "支付服务异常", rule_summary: "5 分钟内 3 条告警",
};

describe("Incident 中文展示投影", () => {
  it("把列表状态和严重级别转成中文", () => {
    const view = toIncidentListItem(incident);
    expect(view.stateLabel).toBe("待确认");
    expect(view.severityLabel).toBe("严重");
    expect(view.environmentLabel).toBe("生产环境");
    expect(view.alertSummary).toBe("3 条活跃 / 共 5 条");
  });

  it("把活动类型和操作者转成可理解的时间线", () => {
    const view = toIncidentActivityView({
      id: "iact_1", kind: "FEISHU_MESSAGE_RECORDED", actor_type: "FEISHU",
      actor: "feishu:user:abcd", summary: "数据库连接池已检查", occurred_at: "2026-09-01T01:12:00Z", metadata: {},
    });
    expect(view.kindLabel).toBe("飞书沟通已记录");
    expect(view.actorLabel).toBe("飞书群成员");
    expect(view.summary).toBe("数据库连接池已检查");
  });

  it("详情投影只使用后端返回的真实事实", () => {
    const view = toIncidentDetailView({
      incident, rule_name: "支付服务异常", rule_summary: "5 分钟内 3 条告警",
      alerts: [{ id: "alt_1", alert_name: "HighLatency", state: "FIRING", severity: "high", environment: "production", service: "checkout", entity_type: "service", entity_display_name: "checkout", source_id: "src_1", source_name: "Prometheus", summary: "延迟升高", description: "P99 超过阈值", first_observed_at: "2026-09-01T01:00:00Z", last_observed_at: "2026-09-01T01:10:00Z", first_received_at: "2026-09-01T01:00:01Z", last_received_at: "2026-09-01T01:10:01Z", resolved_at: null }],
      alerts_truncated: false, activities: [], activities_truncated: false,
      feishu: { configured: true, route_name: "生产事故群", chat_id_masked: "oc_***123", thread_bound: true, last_synced_at: null, notification_state: "SENT", last_error_code: null },
    });
    expect(view.alerts[0]).toMatchObject({ name: "HighLatency", stateLabel: "告警中", sourceName: "Prometheus" });
    expect(view.feishu.statusLabel).toBe("已建立协同线程");
  });

  it("通知路由隐藏完整 chat_id", () => {
    const view = toNotificationRouteView({ environment: "production", chat_id: "oc_1234567890", chat_name: "生产事故群", enabled: true, version: 1 });
    expect(view.chatIdMasked).toBe("oc_1…7890");
    expect(view.environmentLabel).toBe("生产环境");
  });
});
