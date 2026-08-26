const typeMap = { ALERTMANAGER: ["Prometheus / Alertmanager", "alertmanager"], CLOUDEVENTS: ["CloudEvents", "cloudevents"], MANUAL: ["平台内置", "manual"] };
const outcomeMap = { ACCEPTED: ["接收成功", "success"], REPLAYED: ["重复请求已识别", "success"], VALIDATED: ["验证通过", "success"], PAYLOAD_REJECTED: ["请求被拒绝", "failed"], SOURCE_DISABLED: ["来源已停用", "failed"], PROCESSING_FAILED: ["处理失败", "failed"] };
function time(value) { if (!value) return "尚无记录"; const date = new Date(value); return Number.isNaN(date.getTime()) ? "时间未知" : new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date).replaceAll("/", "-"); }

export function toAlertSourceListItem(source) {
  const [type, adapter] = typeMap[source.source_type] ?? ["其他来源", "unknown"];
  const receiveState = source.state === "DISABLED" ? "已停用" : source.last_rejected_at && (!source.last_accepted_at || new Date(source.last_rejected_at) > new Date(source.last_accepted_at)) ? "最近接收失败" : source.last_accepted_at ? "已收到数据" : "等待首次数据";
  return { id: source.id, name: source.name, type, adapter, management: source.management_type === "SYSTEM_MANAGED" ? "系统管理" : "用户管理", editable: source.management_type === "USER_MANAGED", enabled: source.state === "ENABLED", state: source.state === "ENABLED" ? "已启用" : "已停用", receiveState, lastReceivedAt: time(source.last_accepted_at), acceptedRequests: source.accepted_requests, rejectedRequests: source.rejected_requests, version: source.version };
}
export function toAlertSourceDetail(source) {
  const view = toAlertSourceListItem(source);
  return { ...view, webhookPath: source.management_type === "USER_MANAGED" ? `/api/v1/intake/${view.adapter}/${source.id}` : "系统兼容入口", counts: { opened: source.opened_count, updated: source.updated_count, resolved: source.resolved_count, replayed: source.replayed_count, ignored: source.ignored_count }, credentials: (source.credentials ?? []).map((item) => ({ id: item.id, state: item.state === "ACTIVE" ? "有效" : "已撤销", active: item.state === "ACTIVE", createdAt: time(item.created_at), lastUsedAt: time(item.last_used_at), revokedAt: time(item.revoked_at) })) };
}
export function toReceiptView(receipt) { const [outcome, tone] = outcomeMap[receipt.outcome] ?? ["未知结果", "failed"]; return { id: receipt.id, outcome, tone, reason: receipt.reason_code, inputCount: receipt.input_count, opened: receipt.opened_count, updated: receipt.updated_count, resolved: receipt.resolved_count, replayed: receipt.replayed_count, ignored: receipt.ignored_count, receivedAt: time(receipt.received_at) }; }
