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
    state_changed_at: "2026-08-25T08:00:02Z",
    resolved_at: null,
    closed_at: null,
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
    alert_total: 101,
    alerts_truncated: false,
    correlation: {
      outcome: "LINKED_EXACT_SERVICE",
      rule_version: "correlation.v1",
      reason_codes: ["one_exact_service_candidate"],
      explanation: "窗口内只有一个同服务事故，已自动关联。",
      created_at: "2026-08-25T08:05:01Z",
    },
    activities: [
      {
        id: "iact_1",
        kind: "NOTE_ADDED",
        actor: "manual-api-client",
        from_state: null,
        to_state: null,
        note_category: "CURRENT_FINDING",
        message: "错误集中在两个实例",
        resolution_category: null,
        resolution_actions: null,
        root_cause: null,
        incident_version: 2,
        created_at: "2026-08-25T08:09:00Z",
      },
    ],
    activities_truncated: false,
    allowed_actions: ["ADD_NOTE", "TRANSITION", "RESOLVE"],
    allowed_transitions: ["TRIAGING", "INVESTIGATING"],
    primary_action: { action: "TRANSITION", target_state: "TRIAGING" },
    timeline: [
      {
        id: "created:1",
        kind: "incident_created",
        occurred_at: "2026-08-25T08:00:02Z",
        title: "创建事故",
        detail: "系统根据已持久化告警创建事故",
      },
      {
        id: "claimed:1",
        kind: "incident_claimed",
        occurred_at: "2026-08-25T08:10:00Z",
        title: "事故已认领",
        detail: "manual-api-client",
      },
    ],
  };

  const view = toIncidentDetail(overview, new Date("2026-08-25T08:20:00Z"));

  expect(view.reason).toBe("窗口内只有一个同服务事故，已自动关联。");
  expect(view.alerts).toHaveLength(1);
  expect(view.alertTotal).toBe(101);
  expect(view.impact).toContain("当前关联 101 条告警");
  expect(view.alerts[0]).toMatchObject({ state: "触发中", source: "Alertmanager" });
  expect(view.timeline).toHaveLength(2);
  expect(view.timeline[0].title).toBe("创建事故");
  expect(view.timeline[1].detail).toBe("操作员已认领事故");
  expect(view.timeline[1].detail).not.toContain("manual-api-client");
  expect(view.allowedActions).toEqual(["添加处置记录", "推进状态", "解决事故"]);
  expect(view.allowedActionCodes).toEqual(["ADD_NOTE", "TRANSITION", "RESOLVE"]);
  expect(view.primaryAction).toMatchObject({
    label: "推进到分诊中",
    targetState: "TRIAGING",
  });
  expect(view.activities[0]).toMatchObject({
    title: "添加处置记录",
    category: "当前发现",
    actor: "操作员",
    rootCause: "尚未确认",
  });
  expect(JSON.stringify(view)).not.toContain("manual-api-client");
});

it("无正文的认领活动使用固定中文摘要", () => {
  const overview = {
    ...listItem,
    assignee: "local-actor",
    claimed_at: listItem.detected_at,
    created_at: listItem.detected_at,
    state_changed_at: listItem.detected_at,
    resolved_at: null,
    closed_at: null,
    alerts: [],
    alerts_truncated: false,
    correlation: null,
    timeline: [],
    activities: [{
      id: "iact_claim", kind: "INCIDENT_CLAIMED", actor: "local-actor",
      from_state: null, to_state: null, note_category: null, message: null,
      resolution_category: null, resolution_actions: null, root_cause: null,
      incident_version: 2, created_at: listItem.detected_at,
    }],
    activities_truncated: false,
    allowed_actions: ["RELEASE"],
    allowed_transitions: [],
    primary_action: null,
  };

  const view = toIncidentDetail(overview);

  expect(view.activities[0].message).toBe("事故由操作员认领");
  expect(view.owner).toBe("当前操作员");
});

it("未知活动和操作使用安全中文兜底", () => {
  const overview = {
    ...listItem,
    claimed_at: null,
    created_at: listItem.detected_at,
    state_changed_at: listItem.detected_at,
    resolved_at: null,
    closed_at: null,
    alerts: [],
    alerts_truncated: false,
    correlation: null,
    timeline: [],
    activities: [{
      id: "iact_unknown", kind: "UNKNOWN_KIND", actor: "remote-actor",
      from_state: null, to_state: null, note_category: null, message: null,
      resolution_category: null, resolution_actions: null, root_cause: null,
      incident_version: 2, created_at: listItem.detected_at,
    }],
    activities_truncated: false,
    allowed_actions: ["UNKNOWN_ACTION"],
    allowed_transitions: [],
    primary_action: { action: "UNKNOWN_ACTION", target_state: null },
  };

  const view = toIncidentDetail(overview);

  expect(view.allowedActions).toEqual(["未知操作"]);
  expect(view.primaryAction.label).toBe("未知操作");
  expect(view.activities[0].title).toBe("未知处置活动");
  expect(view.activities[0].actor).toBe("操作员");
});
