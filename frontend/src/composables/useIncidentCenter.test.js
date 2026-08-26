import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  executeIncidentAction,
  fetchIncidentOverview,
  fetchIncidents,
  IncidentApiError,
} from "../api/incidents";
import { useIncidentCenter } from "./useIncidentCenter";

vi.mock("../api/incidents", async (importOriginal) => {
  const original = await importOriginal();
  return {
    ...original,
    executeIncidentAction: vi.fn(),
    fetchIncidentOverview: vi.fn(),
    fetchIncidents: vi.fn(),
  };
});

const listItem = {
  id: "inc_1",
  title: "支付服务错误率升高",
  severity: "critical",
  state: "INVESTIGATING",
  service: "payment-api",
  environment: "production",
  assignee: "manual-api-client",
  owner_team: "支付平台组",
  detected_at: "2026-08-26T08:00:00Z",
  last_activity_at: "2026-08-26T08:05:00Z",
  alert_count: 1,
  version: 4,
};

const overview = {
  ...listItem,
  claimed_at: "2026-08-26T08:01:00Z",
  state_changed_at: "2026-08-26T08:02:00Z",
  resolved_at: null,
  closed_at: null,
  created_at: "2026-08-26T08:00:00Z",
  alerts: [],
  alerts_truncated: false,
  correlation: null,
  activities: [],
  activities_truncated: false,
  allowed_actions: ["RELEASE", "TRANSITION", "ADD_NOTE", "RESOLVE"],
  allowed_transitions: ["MITIGATING", "MONITORING_RECOVERY"],
  primary_action: { action: "TRANSITION", target_state: "MITIGATING" },
  timeline: [],
};

function mountCenter() {
  let center;
  const wrapper = mount(defineComponent({
    setup() {
      center = useIncidentCenter();
      return () => h("div");
    },
  }));
  return { wrapper, get center() { return center; } };
}

beforeEach(() => {
  fetchIncidents.mockResolvedValue({ items: [listItem], total: 1, limit: 100, offset: 0 });
  fetchIncidentOverview.mockResolvedValue(overview);
  executeIncidentAction.mockResolvedValue({ version: 5 });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("事故处置前端状态", () => {
  it("写入成功后重新读取列表和详情", async () => {
    const mounted = mountCenter();
    await flushPromises();

    const success = await mounted.center.executeSelectedAction("notes", {
      category: "CURRENT_FINDING",
      message: "错误集中在两个实例",
    });

    expect(success).toBe(true);
    expect(executeIncidentAction).toHaveBeenCalledWith(
      "inc_1",
      "notes",
      {
        expected_version: 4,
        category: "CURRENT_FINDING",
        message: "错误集中在两个实例",
      },
      expect.any(String),
      expect.any(Object),
    );
    expect(fetchIncidents).toHaveBeenCalledTimes(2);
    expect(fetchIncidentOverview).toHaveBeenCalledTimes(2);
    expect(mounted.center.operationState.value).toBe("succeeded");
    mounted.wrapper.unmount();
  });

  it("网络结果未知时保留同一个幂等键用于重试", async () => {
    executeIncidentAction
      .mockRejectedValueOnce(
        new IncidentApiError("incident_api_unavailable", "事故操作结果暂时未知"),
      )
      .mockResolvedValueOnce({ version: 5 });
    const mounted = mountCenter();
    await flushPromises();

    const first = await mounted.center.executeSelectedAction("claim", {});
    const firstKey = executeIncidentAction.mock.calls[0][3];
    const retried = await mounted.center.retryLastAction();

    expect(first).toBe(false);
    expect(retried).toBe(true);
    expect(executeIncidentAction.mock.calls[1][3]).toBe(firstKey);
    expect(mounted.center.retryableOperation.value).toBeNull();
    mounted.wrapper.unmount();
  });

  it("版本冲突不重放旧操作，只在确认后刷新服务端事实", async () => {
    executeIncidentAction.mockRejectedValueOnce(
      new IncidentApiError(
        "incident_version_conflict",
        "事故已被其他操作更新，请刷新后重试",
        409,
      ),
    );
    const mounted = mountCenter();
    await flushPromises();

    const success = await mounted.center.executeSelectedAction("claim", {});

    expect(success).toBe(false);
    expect(mounted.center.operationState.value).toBe("conflict");
    expect(mounted.center.retryableOperation.value).toBeNull();
    expect(fetchIncidents).toHaveBeenCalledTimes(1);

    await mounted.center.refreshAfterConflict();

    expect(fetchIncidents).toHaveBeenCalledTimes(2);
    expect(fetchIncidentOverview).toHaveBeenCalledTimes(2);
    expect(executeIncidentAction).toHaveBeenCalledTimes(1);
    expect(mounted.center.operationState.value).toBe("idle");
    mounted.wrapper.unmount();
  });
});
