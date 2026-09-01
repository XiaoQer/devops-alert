import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  acknowledgeIncident,
  fetchIncident,
  fetchIncidents,
  resolveIncident,
} from "../api/incidents";
import { useIncidents } from "./useIncidents";

vi.mock("../api/incidents", () => ({
  fetchIncidents: vi.fn(), fetchIncident: vi.fn(), acknowledgeIncident: vi.fn(), resolveIncident: vi.fn(),
}));

const incident = {
  id: "inc_1", reference: "INC-20260901-001", title: "production checkout异常",
  state: "OPEN", severity: "high", environment: "production", group_by: "SERVICE",
  group_key: "checkout", group_display_name: "checkout", incident_rule_id: "irl_1",
  incident_rule_version: 1, alert_count: 2, active_alert_count: 2,
  distinct_alert_name_count: 1, version: 1, opened_at: "2026-09-01T01:00:00Z",
  acknowledged_at: null, resolved_at: null, resolution_summary: null,
  last_alert_at: "2026-09-01T01:01:00Z", created_at: "2026-09-01T01:00:00Z",
  updated_at: "2026-09-01T01:01:00Z", rule_name: "支付服务异常", rule_summary: "5 分钟内 2 条",
};
const detail = {
  incident, rule_name: incident.rule_name, rule_summary: incident.rule_summary,
  alerts: [], alerts_truncated: false, activities: [], activities_truncated: false,
  feishu: { configured: false, route_name: null, chat_id_masked: null, thread_bound: false, last_synced_at: null, notification_state: null, last_error_code: null },
};

beforeEach(() => {
  vi.clearAllMocks();
  fetchIncidents.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  fetchIncident.mockResolvedValue(detail);
});

describe("Incident 页面状态", () => {
  it("默认只读取未解决 Incident，不制造演示数据", async () => {
    const state = useIncidents({ autoLoad: false });
    await state.loadIncidents();
    expect(fetchIncidents).toHaveBeenCalledWith(
      expect.objectContaining({ states: ["OPEN", "ACKNOWLEDGED"] }),
      expect.any(Object),
    );
    expect(state.listState.value).toBe("empty");
    expect(state.incidents.value).toEqual([]);
  });

  it("打开 Incident 后保存真实详情", async () => {
    const state = useIncidents({ autoLoad: false });
    await state.openIncident("inc_1");
    expect(state.selectedIncident.value.reference).toBe("INC-20260901-001");
    expect(state.detailState.value).toBe("ready");
  });

  it("确认成功后使用新版本并刷新列表", async () => {
    acknowledgeIncident.mockResolvedValue({ incident: { ...incident, state: "ACKNOWLEDGED", version: 2 }, replayed: false });
    fetchIncident
      .mockResolvedValueOnce(detail)
      .mockResolvedValueOnce({
        ...detail,
        incident: { ...incident, state: "ACKNOWLEDGED", version: 2 },
        activities: [{
          id: "iact_1", kind: "ACKNOWLEDGED", actor_type: "USER",
          actor: "manual-api-client", summary: "INC-20260901-001 已确认",
          occurred_at: "2026-09-01T01:02:00Z", metadata: {},
        }],
      });
    const state = useIncidents({ autoLoad: false });
    await state.openIncident("inc_1");
    expect(await state.acknowledge()).toBe(true);
    expect(state.selectedIncident.value.state).toBe("ACKNOWLEDGED");
    expect(state.selectedIncident.value.version).toBe(2);
    expect(state.detail.value.incident.rule_name).toBe("支付服务异常");
    expect(state.detail.value.activities[0].kindLabel).toBe("Incident 已确认");
    expect(fetchIncident).toHaveBeenCalledTimes(2);
    expect(fetchIncidents).toHaveBeenCalled();
  });

  it("版本冲突后保留解决说明并刷新详情", async () => {
    resolveIncident.mockRejectedValueOnce({ code: "incident_version_conflict", userMessage: "Incident 已更新" });
    const state = useIncidents({ autoLoad: false });
    await state.openIncident("inc_1");
    state.resolutionSummary.value = "数据库连接已恢复";
    expect(await state.resolve()).toBe(false);
    expect(state.operationState.value).toBe("conflict");
    expect(state.resolutionSummary.value).toBe("数据库连接已恢复");
    expect(fetchIncident).toHaveBeenCalledTimes(2);
  });

  it("过短解决说明不会发送请求", async () => {
    const state = useIncidents({ autoLoad: false });
    await state.openIncident("inc_1");
    state.resolutionSummary.value = "   ";
    expect(await state.resolve()).toBe(false);
    expect(state.operationError.value).toBe("请填写解决说明");
    expect(resolveIncident).not.toHaveBeenCalled();
  });
});
