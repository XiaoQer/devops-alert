const stateLabels = { OPEN: "待确认", ACKNOWLEDGED: "处理中", RESOLVED: "已解决" };
const stateTones = { OPEN: "open", ACKNOWLEDGED: "acknowledged", RESOLVED: "resolved" };
const severityLabels = { low: "提示", medium: "一般", high: "重要", critical: "严重" };
const environmentLabels = { production: "生产环境", staging: "预发环境", testing: "测试环境", development: "开发环境" };
const alertStateLabels = { FIRING: "告警中", RESOLVED: "已恢复" };
const activityKindLabels = {
  INCIDENT_CREATED: "Incident 已创建",
  ALERTS_LINKED: "关联告警已更新",
  SEVERITY_ESCALATED: "严重级别已升级",
  ALL_ALERTS_RECOVERED: "关联告警已全部恢复",
  ACKNOWLEDGED: "Incident 已确认",
  RESOLVED: "Incident 已解决",
  FEISHU_MESSAGE_RECORDED: "飞书沟通已记录",
  NOTIFICATION_FAILED: "飞书通知失败",
};
const actorLabels = { SYSTEM: "平台", USER: "处置人员", FEISHU: "飞书群成员" };

export function toIncidentListItem(incident) {
  return {
    ...incident,
    stateLabel: stateLabels[incident.state] ?? "状态未知",
    stateTone: stateTones[incident.state] ?? "unknown",
    severityLabel: severityLabels[incident.severity] ?? "未知级别",
    environmentLabel: environmentLabels[incident.environment] ?? incident.environment ?? "环境未知",
    alertSummary: `${incident.active_alert_count ?? 0} 条活跃 / 共 ${incident.alert_count ?? 0} 条`,
    updatedAtLabel: formatDateTime(incident.updated_at),
    openedAtLabel: formatDateTime(incident.opened_at),
  };
}

export function toIncidentAlertView(alert) {
  return {
    ...alert,
    name: alert.alert_name,
    stateLabel: alertStateLabels[alert.state] ?? "状态未知",
    severityLabel: severityLabels[alert.severity] ?? "未知级别",
    environmentLabel: environmentLabels[alert.environment] ?? alert.environment ?? "环境未知",
    sourceName: alert.source_name,
    entityName: alert.entity_display_name || alert.service || "未识别对象",
    firstReceivedAtLabel: formatDateTime(alert.first_received_at),
    lastReceivedAtLabel: formatDateTime(alert.last_received_at),
  };
}

export function toIncidentActivityView(activity) {
  return {
    ...activity,
    kindLabel: activityKindLabels[activity.kind] ?? "处置记录",
    actorLabel: actorLabels[activity.actor_type] ?? "相关人员",
    occurredAtLabel: formatDateTime(activity.occurred_at),
  };
}

export function toIncidentDetailView(detail) {
  const incident = toIncidentListItem({
    ...detail.incident,
    rule_name: detail.rule_name,
    rule_summary: detail.rule_summary,
  });
  return {
    ...detail,
    incident,
    alerts: (detail.alerts ?? []).map(toIncidentAlertView),
    activities: (detail.activities ?? []).map(toIncidentActivityView),
    feishu: {
      ...detail.feishu,
      statusLabel: feishuStatusLabel(detail.feishu),
      lastSyncedAtLabel: formatDateTime(detail.feishu?.last_synced_at),
    },
  };
}

export function toNotificationRouteView(route) {
  return {
    ...route,
    environmentLabel: environmentLabels[route.environment] ?? route.environment ?? "环境未知",
    chatIdMasked: maskChatId(route.chat_id),
    enabledLabel: route.enabled ? "已启用" : "已停用",
    updatedAtLabel: formatDateTime(route.updated_at),
  };
}

function feishuStatusLabel(feishu = {}) {
  if (!feishu.configured) return "未配置飞书事故群";
  if (feishu.thread_bound) return "已建立协同线程";
  if (feishu.notification_state === "FAILED") return "通知暂未送达";
  return "等待建立协同线程";
}

function maskChatId(chatId) {
  if (!chatId) return "—";
  if (chatId.length <= 8) return `${chatId.slice(0, 2)}…${chatId.slice(-2)}`;
  return `${chatId.slice(0, 4)}…${chatId.slice(-4)}`;
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(date);
}
