import { onBeforeUnmount, ref, unref, watch } from "vue";

import { fetchEvidenceRun, fetchEvidenceRuns, requestEvidenceRun } from "../api/incidentEvidence";
import { toEvidenceDetailView, toEvidenceRunView } from "../presentation/evidenceView";

const safeMessage = (error, fallback) => typeof error?.userMessage === "string" ? error.userMessage : fallback;
const operationKey = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

export function useIncidentEvidence(incidentId, { autoLoad = true } = {}) {
  const runs = ref([]);
  const total = ref(0);
  const selectedRunId = ref("");
  const detail = ref(null);
  const state = ref("idle");
  const error = ref("");
  const mutationState = ref("idle");
  const mutationError = ref("");
  let controller;
  let pollTimer;

  async function load(preferredRunId = "") {
    const id = unref(incidentId);
    if (!id) return false;
    controller?.abort();
    controller = new AbortController();
    clearTimeout(pollTimer);
    state.value = "loading";
    error.value = "";
    try {
      const page = await fetchEvidenceRuns(id, { limit: 50, signal: controller.signal });
      runs.value = (page.items ?? []).map(toEvidenceRunView);
      total.value = page.total ?? runs.value.length;
      const runId = preferredRunId || selectedRunId.value || runs.value[0]?.id || "";
      if (!runId) {
        selectedRunId.value = "";
        detail.value = null;
        state.value = "empty";
        return true;
      }
      await selectRun(runId, { signal: controller.signal });
      scheduleRefresh();
      return true;
    } catch (loadError) {
      if (loadError?.name === "AbortError") return false;
      state.value = "error";
      error.value = safeMessage(loadError, "监控取证暂时无法读取");
      return false;
    }
  }

  async function selectRun(runId, options = {}) {
    const id = unref(incidentId);
    if (!id || !runId) return false;
    try {
      const response = await fetchEvidenceRun(id, runId, options);
      selectedRunId.value = runId;
      detail.value = toEvidenceDetailView(response);
      state.value = "ready";
      return true;
    } catch (loadError) {
      if (loadError?.name === "AbortError") return false;
      state.value = "error";
      error.value = safeMessage(loadError, "取证详情暂时无法读取");
      return false;
    }
  }

  async function requestNewRun() {
    const id = unref(incidentId);
    if (!id || mutationState.value === "pending") return false;
    mutationState.value = "pending";
    mutationError.value = "";
    try {
      const response = await requestEvidenceRun(id, operationKey());
      await selectRun(response.run.id);
      mutationState.value = "succeeded";
      if (autoLoad) scheduleRefresh();
      return true;
    } catch (mutationFailure) {
      const activeRunId = mutationFailure?.details?.active_run_id;
      if (mutationFailure?.code === "evidence_run_already_active" && activeRunId) {
        await load(activeRunId);
        mutationState.value = "conflict";
        mutationError.value = "已有取证正在执行，已切换到当前进度";
        return false;
      }
      mutationState.value = "error";
      mutationError.value = safeMessage(mutationFailure, "重新取证未能启动");
      return false;
    }
  }

  function scheduleRefresh() {
    clearTimeout(pollTimer);
    if (!autoLoad || !detail.value?.run?.isActive) return;
    pollTimer = setTimeout(() => load(selectedRunId.value), 2000);
  }

  if (autoLoad) {
    watch(() => unref(incidentId), () => load(), { immediate: true });
    onBeforeUnmount(() => { controller?.abort(); clearTimeout(pollTimer); });
  }

  return {
    runs, total, selectedRunId, detail, state, error, mutationState, mutationError,
    load, selectRun, requestNewRun,
  };
}
