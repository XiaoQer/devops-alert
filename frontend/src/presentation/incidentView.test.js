import { describe, expect, it } from "vitest";

import { toIncidentDetail, toIncidentListItem } from "./incidentView";

const listItem = {
  id: "inc_0123456789abcdef0123456789abcdef",
  title: "真实支付告警",
  severity: "critical",
  state: "DETECTED",
  service: "payment-api",
  environment: "production",
  assignee: null,
  owner_team: "支付平台组",
  detected_at: "2026-08-25T08:00:00Z",
  last_activity_at: "2026-08-25T08:05:00Z",
  alert_count: 2,
  version: 1,
};

it("把真实列表字段转换为中文队列视图", () => {
  const view = toIncidentListItem(listItem, new Date("2026-08-25T08:20:00Z"));

  expect(view).toMatchObject({
    title: "真实支付告警",
    severity: "严重",
    state: "待处置",
    stateTone: "active",
    team: "支付平台组",
    owner: "未认领",
    alertCount: 2,
    duration: "20 分钟",
  });
});

it("详情只转换后端已经持久化的告警、原因和时间线", () => {
  const overview = {
    ...listItem,
    claimed_at: null,
    created_at: "2026-08-25T08:00:02Z",
    alerts: [
      {
        id: "alt_1",
        title: "支付 5xx 错误率升高",
        state: "ACTIVE",
        severity: "critical",
        source: "alertmanager",
        first_observed_at: "2026-08-25T08:00:00Z",
        last_observed_at: "2026-08-25T08:05:00Z",
        version: 2,
      },
    ],
    alerts_truncated: false,
    correlation: {
      outcome: "LINKED_EXACT_SERVICE",
      rule_version: "correlation.v1",
      reason_codes: ["one_exact_service_candidate"],
      explanation: "窗口内只有一个同服务事故，已自动关联。",
      created_at: "2026-08-25T08:05:01Z",
    },
    timeline: [
      {
        id: "created:1",
        kind: "incident_created",
        occurred_at: "2026-08-25T08:00:02Z",
        title: "创建事故",
        detail: "系统根据已持久化告警创建事故",
      },
    ],
  };

  const view = toIncidentDetail(overview, new Date("2026-08-25T08:20:00Z"));

  expect(view.reason).toBe("窗口内只有一个同服务事故，已自动关联。");
  expect(view.alerts).toHaveLength(1);
  expect(view.alerts[0]).toMatchObject({ state: "触发中", source: "Alertmanager" });
  expect(view.timeline).toHaveLength(1);
  expect(view.timeline[0].title).toBe("创建事故");
});
