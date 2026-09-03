const runStateLabels = {
  QUEUED: "等待取证",
  RUNNING: "正在取证",
  SUCCEEDED: "取证完成",
  PARTIAL: "部分证据缺失",
  FAILED: "取证未完成",
};
const itemStateLabels = {
  SUCCEEDED: "已取得数据",
  NO_DATA: "未发现数据",
  INSUFFICIENT_BASELINE: "基线数据不足",
  MISSING_TARGET: "缺少监控对象",
  SKIPPED_DEPENDENCY: "数据源未配置",
  FAILED: "查询失败",
};
const sourceLabels = {
  PROMETHEUS: "Prometheus",
  ELASTICSEARCH: "ELK 日志",
  SKYWALKING: "SkyWalking",
  PLATFORM: "跨源关联",
};

export function toEvidenceRunView(run = {}) {
  return {
    ...run,
    stateLabel: runStateLabels[run.state] ?? "状态未知",
    triggerLabel: run.trigger === "MANUAL" ? "人工重新取证" : "Incident 自动取证",
    createdAtLabel: formatDateTime(run.created_at),
    completedAtLabel: formatDateTime(run.completed_at),
    isActive: ["QUEUED", "RUNNING"].includes(run.state),
  };
}

export function toEvidenceItemView(item = {}) {
  const needsAttention = ["INSUFFICIENT_BASELINE", "MISSING_TARGET", "SKIPPED_DEPENDENCY", "FAILED"].includes(item.state);
  return {
    ...item,
    stateLabel: itemStateLabels[item.state] ?? "状态未知",
    sourceLabel: sourceLabels[item.source_type] ?? item.source_type ?? "未知来源",
    needsAttention,
    tone: needsAttention ? "attention" : item.state === "NO_DATA" ? "empty" : "normal",
  };
}

export function toEvidenceDetailView(detail = {}) {
  const items = (detail.items ?? []).map(toEvidenceItemView);
  return {
    ...detail,
    run: toEvidenceRunView(detail.run),
    items,
    keyFacts: items
      .filter((item) => item.state === "SUCCEEDED" && item.evidence_type === "METRIC_COMPARISON" && typeof item.interpretation === "string" && item.interpretation.trim())
      .slice(0, 5)
      .map((item) => ({
        id: item.id || item.evidence_key,
        title: item.display_name,
        text: item.interpretation,
        sourceLabel: item.sourceLabel,
      })),
  };
}

export function sourceStatus(items = [], sourceType) {
  const sourceItems = items.filter((item) => item.source_type === sourceType);
  if (!sourceItems.length) return { label: "未执行", tone: "empty" };
  if (sourceItems.some((item) => item.needsAttention)) return { label: "部分缺失", tone: "attention" };
  return { label: "已完成", tone: "normal" };
}

export const evidenceSourceLabels = sourceLabels;

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(date);
}
