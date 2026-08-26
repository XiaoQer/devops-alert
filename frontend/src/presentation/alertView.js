const severityMap = {
  critical: ["严重", "critical"], high: ["重要", "high"],
  medium: ["一般", "medium"], low: ["提示", "medium"],
};
const stateMap = {
  ACTIVE: ["告警中", "active"], RESOLVED: ["已恢复", "resolved"], SUPPRESSED: ["已抑制", "muted"],
};
const environmentMap = { production: "生产环境", staging: "预发环境", development: "开发环境" };
const sourceTypeMap = { ALERTMANAGER: "Prometheus / Alertmanager", CLOUDEVENTS: "CloudEvents", MANUAL: "人工接入" };
const stepTitleMap = { "告警已接入": "告警已认证接入" };

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date).replaceAll("/", "-");
}

function baseView(alert) {
  const [severity, severityTone] = severityMap[alert.severity] ?? ["未知", "medium"];
  const [state, stateTone] = stateMap[alert.state] ?? ["未知状态", "muted"];
  return {
    id: alert.id, title: alert.title, severity, severityTone, state, stateTone,
    service: alert.service, environment: environmentMap[alert.environment] ?? "未知环境",
    sourceName: alert.source?.name ?? "未知来源",
    sourceType: sourceTypeMap[alert.source?.source_type] ?? "其他来源",
    firstObservedAt: formatDateTime(alert.first_observed_at),
    lastObservedAt: formatDateTime(alert.last_observed_at),
    signalCount: alert.signal_count ?? alert.signals?.length ?? 1,
    incidentId: alert.incident_id ?? alert.correlation?.incident?.id ?? null,
    linked: Boolean(alert.incident_id ?? alert.correlation?.incident?.id),
    version: alert.version,
  };
}

export function toAlertListItem(alert) { return baseView(alert); }

export function toAlertSummary(summary) {
  return {
    window: summary.window, active: summary.active, severeActive: summary.severe_active,
    resolved: summary.resolved, unlinkedActive: summary.unlinked_active,
    calculatedAt: formatDateTime(summary.calculated_at),
    bySource: (summary.by_source ?? []).map((item) => ({ name: item.source.name, active: item.active })),
  };
}

export function toAlertDetail(overview) {
  return {
    ...baseView(overview),
    resultText: overview.detection?.summary ?? "暂无可展示的检测结果",
    detectedAt: formatDateTime(overview.detection?.observed_at),
    receivedAt: formatDateTime(overview.detection?.received_at),
    facts: Object.entries(overview.detection?.facts ?? {}).sort(([left], [right]) => left.localeCompare(right)).map(([name, value]) => ({ name, value: String(value) })),
    signalCount: overview.signals?.length ?? 0,
    signalsTruncated: Boolean(overview.signals_truncated),
    correlationText: overview.correlation?.explanation ?? "尚无事故关联结论。",
    correlationStatus: overview.correlation?.status ?? "WAITING",
    incident: overview.correlation?.incident ?? null,
    steps: (overview.processing_steps ?? []).map((step) => ({ ...step, title: stepTitleMap[step.title] ?? step.title })),
  };
}
