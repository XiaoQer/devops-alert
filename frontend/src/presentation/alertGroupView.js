const severityMap = {
  critical: ["严重", "critical"], high: ["重要", "high"],
  medium: ["一般", "medium"], low: ["提示", "medium"],
};
const stateMap = { ACTIVE: ["告警中", "active"], RESOLVED: ["已恢复", "resolved"] };
const environmentMap = { production: "生产环境", staging: "预发环境", development: "开发环境" };
const symptomMap = {
  errors: "错误异常", latency: "响应延迟", availability: "可用性下降",
  saturation: "资源饱和", "pod-restarts": "Pod 重启", cpu: "CPU 过载",
  memory: "内存异常", unknown: "异常信号",
};

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(date).replaceAll("/", "-");
}

function formatDuration(start, end) {
  const milliseconds = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "持续时间未知";
  const minutes = Math.max(1, Math.ceil(milliseconds / 60_000));
  if (minutes < 60) return `持续 ${minutes} 分钟`;
  const hours = Math.floor(minutes / 60); const remainder = minutes % 60;
  return remainder ? `持续 ${hours} 小时 ${remainder} 分钟` : `持续 ${hours} 小时`;
}

function baseGroup(group) {
  const [severity, severityTone] = severityMap[group.severity] ?? ["未知", "medium"];
  const [state, stateTone] = stateMap[group.state] ?? ["未知状态", "muted"];
  return {
    id: group.id, title: group.title, severity, severityTone, state, stateTone,
    service: group.service || "未知服务",
    environment: environmentMap[group.environment] ?? "未知环境",
    symptom: symptomMap[group.symptom] ?? group.symptom ?? "异常信号",
    activeCount: group.active_count ?? 0, totalCount: group.total_count ?? 0,
    resourceCount: group.impacted_resource_count ?? 0,
    memberText: `${group.total_count ?? 0} 条原始告警`,
    activeText: `${group.active_count ?? 0} 条仍在告警`,
    resourceText: `影响 ${group.impacted_resource_count ?? 0} 个资源`,
    storm: group.storm_state === "STORM",
    stormText: group.storm_state === "STORM" ? "告警风暴" : "正常流量",
    incident: group.incident ?? null,
    incidentText: group.incident ? "已关联事故" : "待关联事故",
    firstObservedAt: formatDateTime(group.first_observed_at),
    lastObservedAt: formatDateTime(group.last_observed_at),
    duration: formatDuration(group.first_observed_at, group.last_observed_at),
    explanation: group.explanation || "暂无归组说明",
  };
}

export function toAlertGroupListItem(group) { return baseGroup(group); }

export function toAlertGroupSummary(summary) {
  return {
    activeGroups: summary.active_groups, severeActiveGroups: summary.severe_active_groups,
    activeAlerts: summary.active_alerts, stormGroups: summary.storm_groups,
    resolvedGroups: summary.resolved_groups, pendingJobs: summary.pending_group_jobs,
    compressionText: `${Number(summary.compression_ratio ?? 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}:1`,
    peakText: `${summary.peak_rate_per_minute ?? 0} 条/分钟`,
    calculatedAt: formatDateTime(summary.calculated_at),
  };
}

export function toAlertGroupDetail(overview) {
  return {
    ...baseGroup(overview.group),
    reason: overview.group?.explanation || "暂无归组说明",
    sources: (overview.source_distribution ?? []).map((item) => ({ name: item.name, count: item.count })),
    severities: (overview.severity_distribution ?? []).map((item) => ({
      name: severityMap[item.name]?.[0] ?? item.name, count: item.count,
    })),
    resources: (overview.impacted_resources ?? []).map((item) => ({
      type: item.resource_type, name: item.resource_name, count: item.count,
    })),
  };
}

export function toAlertGroupMember(alert) {
  const [severity, severityTone] = severityMap[alert.severity] ?? ["未知", "medium"];
  const [state, stateTone] = stateMap[alert.state] ?? ["未知状态", "muted"];
  return {
    id: alert.id, title: alert.title, severity, severityTone, state, stateTone,
    sourceName: alert.source_name ?? "未知来源", service: alert.service ?? "未知服务",
    environment: environmentMap[alert.environment] ?? "未知环境",
    firstObservedAt: formatDateTime(alert.first_observed_at),
    lastObservedAt: formatDateTime(alert.last_observed_at),
  };
}
