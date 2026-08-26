const severityMap = {
  critical: ["严重", "critical"],
  high: ["重要", "high"],
  medium: ["一般", "medium"],
  low: ["提示", "medium"],
};

const stateMap = {
  DETECTED: ["待处置", "active"],
  TRIAGING: ["分诊中", "active"],
  INVESTIGATING: ["调查中", "active"],
  MITIGATING: ["缓解中", "active"],
  MONITORING_RECOVERY: ["恢复观察", "active"],
  RESOLVED: ["已解决", "resolved"],
  CLOSED: ["已关闭", "resolved"],
};

const alertStateMap = { ACTIVE: "触发中", RESOLVED: "已恢复", SUPPRESSED: "已抑制" };
const sourceMap = {
  alertmanager: ["Alertmanager", "prometheus"],
  cloudevents: ["CloudEvents", "cloud"],
  manual: ["人工报告", "cloud"],
};

const actionMap = {
  CLAIM: "认领事故",
  RELEASE: "解除认领",
  TRANSITION: "推进状态",
  ADD_NOTE: "添加处置记录",
  RESOLVE: "解决事故",
  REOPEN: "重新打开",
  CLOSE: "关闭事故",
};

const activityMap = {
  INCIDENT_CLAIMED: ["事故已认领", "current"],
  INCIDENT_RELEASED: ["已解除认领", "neutral"],
  STATE_TRANSITIONED: ["处置阶段已更新", "current"],
  NOTE_ADDED: ["添加处置记录", "neutral"],
  INCIDENT_RESOLVED: ["事故已解决", "resolved"],
  INCIDENT_REOPENED: ["事故已重新打开", "current"],
  INCIDENT_CLOSED: ["事故已关闭", "resolved"],
};

const noteCategoryMap = {
  CURRENT_FINDING: "当前发现",
  ACTION_TAKEN: "已执行操作",
  ACTION_RESULT: "操作结果",
  NEXT_STEP: "后续计划",
  GENERAL: "普通备注",
};

const resolutionCategoryMap = {
  RECOVERED: "故障恢复",
  FALSE_POSITIVE: "误报",
  DUPLICATE: "重复告警",
  NO_ACTION: "无需处理",
  OTHER: "其他",
};

function actorLabel(actor) {
  if (!actor) return "操作员";
  return actor === "manual-api-client" ? "当前操作员" : "操作员";
}

function safeTimelineDetail(event) {
  if (event.kind === "incident_claimed") return "由当前操作员认领";
  return String(event.detail ?? "").replaceAll("manual-api-client", "当前操作员");
}

function toPrimaryAction(action) {
  if (!action) return null;
  const label = action.action === "TRANSITION"
    ? `推进到${stateMap[action.target_state]?.[0] ?? "未知状态"}`
    : (actionMap[action.action] ?? "未知操作");
  return {
    action: action.action,
    label,
    targetState: action.target_state ?? null,
  };
}

function toActivity(activity) {
  const [title, tone] = activityMap[activity.kind] ?? ["未知处置活动", "neutral"];
  return {
    id: activity.id,
    title,
    tone,
    actor: actorLabel(activity.actor),
    category: activity.note_category
      ? (noteCategoryMap[activity.note_category] ?? "未知分类")
      : null,
    resolutionCategory: activity.resolution_category
      ? (resolutionCategoryMap[activity.resolution_category] ?? "未知分类")
      : null,
    message: activity.message ?? "未填写说明",
    actions: activity.resolution_actions ?? null,
    rootCause: activity.root_cause ?? "尚未确认",
    fromState: activity.from_state ? (stateMap[activity.from_state]?.[0] ?? "未知状态") : null,
    toState: activity.to_state ? (stateMap[activity.to_state]?.[0] ?? "未知状态") : null,
    version: activity.incident_version,
    occurredAt: formatDateTime(activity.created_at),
    time: formatClock(activity.created_at),
  };
}

function dateValue(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDateTime(value) {
  const date = dateValue(value);
  if (!date) return "时间未知";
  const parts = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`;
}

function formatClock(value) {
  const date = dateValue(value);
  if (!date) return "--:--";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(date);
}

function formatDuration(start, now) {
  const date = dateValue(start);
  const minutes = date ? Math.max(0, Math.floor((now.getTime() - date.getTime()) / 60_000)) : 0;
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
}

export function toIncidentListItem(item, now = new Date()) {
  const [severity, severityTone] = severityMap[item.severity] ?? ["未知", "medium"];
  const [state, stateTone] = stateMap[item.state] ?? ["未知状态", "active"];
  return {
    id: item.id,
    title: item.title,
    service: item.service,
    team: item.owner_team ?? "未配置团队",
    owner: item.assignee ?? "未认领",
    severity,
    severityTone,
    state,
    stateTone,
    environment: item.environment,
    detectedAt: formatDateTime(item.detected_at),
    queueTime: formatClock(item.detected_at).slice(0, 5),
    duration: formatDuration(item.detected_at, now),
    lastActivityAt: formatDateTime(item.last_activity_at),
    alertCount: item.alert_count,
    version: item.version,
  };
}

export function toIncidentDetail(overview, now = new Date()) {
  const latestActivity = overview.activities?.at(-1)?.created_at;
  const base = toIncidentListItem(
    { ...overview, last_activity_at: latestActivity
        ?? overview.alerts.at(-1)?.last_observed_at ?? overview.detected_at,
      alert_count: overview.alerts.length },
    now,
  );
  const [state] = stateMap[overview.state] ?? ["未知状态"];
  return {
    ...base,
    owner: overview.assignee ? actorLabel(overview.assignee) : "未认领",
    claimedAt: overview.claimed_at ? formatDateTime(overview.claimed_at) : null,
    stateChangedAt: formatDateTime(overview.state_changed_at),
    resolvedAt: overview.resolved_at ? formatDateTime(overview.resolved_at) : null,
    closedAt: overview.closed_at ? formatDateTime(overview.closed_at) : null,
    impact: `当前关联 ${overview.alerts.length} 条告警，事故状态为${state}`,
    reason: overview.correlation?.explanation ?? "暂无可展示的关联决策。",
    ruleVersion: overview.correlation?.rule_version ?? null,
    reasonCodes: overview.correlation?.reason_codes ?? [],
    alertsTruncated: overview.alerts_truncated,
    activitiesTruncated: Boolean(overview.activities_truncated),
    allowedActionCodes: overview.allowed_actions ?? [],
    allowedActions: (overview.allowed_actions ?? []).map(
      (action) => actionMap[action] ?? "未知操作",
    ),
    allowedTransitions: (overview.allowed_transitions ?? []).map((stateCode) => ({
      code: stateCode,
      label: stateMap[stateCode]?.[0] ?? "未知状态",
    })),
    primaryAction: toPrimaryAction(overview.primary_action),
    activities: (overview.activities ?? []).map(toActivity),
    alerts: overview.alerts.map((alert) => {
      const [source, sourceType] = sourceMap[alert.source] ?? ["其他来源", "cloud"];
      return {
        id: alert.id,
        name: alert.title,
        state: alertStateMap[alert.state] ?? "未知状态",
        firstSeen: formatClock(alert.first_observed_at),
        duration: formatDuration(alert.first_observed_at, now),
        source,
        sourceType,
      };
    }),
    timeline: overview.timeline.map((event) => ({
      id: event.id,
      time: formatClock(event.occurred_at),
      title: event.title,
      detail: safeTimelineDetail(event),
      tone: event.kind === "incident_created" ? "danger"
        : event.kind === "incident_claimed" ? "current" : "neutral",
    })),
  };
}
