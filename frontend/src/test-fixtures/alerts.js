export const apiAlert = (overrides = {}) => ({
  id: "alt_1", title: "支付错误率升高", state: "ACTIVE", severity: "high",
  service: "payment-api", environment: "production",
  source: { id: "src_1", name: "生产 Prometheus", source_type: "ALERTMANAGER", management_type: "USER_MANAGED" },
  first_observed_at: "2026-08-26T08:00:00Z", last_observed_at: "2026-08-26T08:05:00Z",
  state_changed_at: "2026-08-26T08:00:00Z", signal_count: 2, incident_id: "inc_1", version: 2,
  ...overrides,
});

export const apiOverview = (overrides = {}) => ({
  ...apiAlert(),
  detection: { title: "支付错误率升高", summary: "实际错误率达到 18.4%", observed_at: "2026-08-26T08:00:00Z", received_at: "2026-08-26T08:00:02Z", facts: { value: "18.4%", threshold: "10%" } },
  signals: [{ id: "sig_1", event_type: "prometheus.alert", summary: "实际错误率达到 18.4%", severity: "high", observed_at: "2026-08-26T08:00:00Z", received_at: "2026-08-26T08:00:02Z", facts: {} }],
  signals_truncated: false,
  correlation: { status: "COMPLETED", job_state: "SUCCEEDED", outcome: "LINKED", explanation: "已关联到支付服务事故。", reason_codes: ["same_service"], incident: { id: "inc_1", title: "支付服务异常", state: "INVESTIGATING", severity: "high" } },
  processing_steps: [
    { title: "告警已接入", detail: "已认证并完成标准化。", status: "completed" },
    { title: "重复信号已归并", detail: "已归并 2 条信号。", status: "completed" },
    { title: "事故关联已完成", detail: "已关联到支付服务事故。", status: "completed" },
  ], ...overrides,
});
