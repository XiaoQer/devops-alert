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
  const base = toIncidentListItem(
    { ...overview, last_activity_at: overview.alerts.at(-1)?.last_observed_at ?? overview.detected_at,
      alert_count: overview.alerts.length },
    now,
  );
  const [state] = stateMap[overview.state] ?? ["未知状态"];
  return {
    ...base,
    claimedAt: overview.claimed_at ? formatDateTime(overview.claimed_at) : null,
    impact: `当前关联 ${overview.alerts.length} 条告警，事故状态为${state}`,
    reason: overview.correlation?.explanation ?? "暂无可展示的关联决策。",
    ruleVersion: overview.correlation?.rule_version ?? null,
    reasonCodes: overview.correlation?.reason_codes ?? [],
    alertsTruncated: overview.alerts_truncated,
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
      detail: event.kind === "incident_claimed" ? "由当前操作员认领" : event.detail,
      tone: event.kind === "incident_created" ? "danger"
        : event.kind === "incident_claimed" ? "current" : "neutral",
    })),
  };
}
