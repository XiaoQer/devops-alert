import { describe, expect, it } from "vitest";

import { toAlertSourceDetail, toAlertSourceListItem, toReceiptView } from "./alertSourceView";

const source = (overrides = {}) => ({ id: "src_1", name: "生产 Prometheus", source_type: "ALERTMANAGER", management_type: "USER_MANAGED", state: "ENABLED", version: 2, last_accepted_at: "2026-08-26T08:00:00Z", last_rejected_at: null, last_validated_at: null, accepted_requests: 3, rejected_requests: 0, opened_count: 2, updated_count: 1, resolved_count: 0, replayed_count: 0, ignored_count: 0, created_at: "2026-08-26T07:00:00Z", updated_at: "2026-08-26T08:00:00Z", credentials: [], ...overrides });

describe("告警源中文视图", () => {
  it("明确显示接收状态和系统管理边界", () => {
    expect(toAlertSourceListItem(source())).toMatchObject({ receiveState: "已收到数据", editable: true });
    expect(toAlertSourceListItem(source({ management_type: "SYSTEM_MANAGED" }))).toMatchObject({ management: "系统管理", editable: false });
    expect(toAlertSourceListItem(source({ last_accepted_at: null }))).toMatchObject({ receiveState: "等待首次数据" });
    expect(toAlertSourceListItem(source({ state: "DISABLED" }))).toMatchObject({ receiveState: "已停用" });
  });

  it("生成专属入口并转换接收结果", () => {
    expect(toAlertSourceDetail(source()).webhookPath).toBe("/api/v1/intake/alertmanager/src_1");
    expect(toReceiptView({ id: "rcp_1", outcome: "PAYLOAD_REJECTED", reason_code: "invalid_payload", input_count: 1, opened_count: 0, updated_count: 0, resolved_count: 0, replayed_count: 0, ignored_count: 0, received_at: "2026-08-26T08:00:00Z" })).toMatchObject({ outcome: "请求被拒绝", tone: "failed" });
  });
});
