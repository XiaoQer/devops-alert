import { onBeforeUnmount, ref, unref, watch } from "vue";

import { createDiagnosisRun, fetchDiagnosisRun, fetchDiagnosisRuns } from "../api/incidentDiagnosis";

const safeMessage = (error, fallback) => typeof error?.userMessage === "string" ? error.userMessage : fallback;
const operationKey = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
const activeStates = new Set(["QUEUED", "RUNNING"]);

export function useIncidentDiagnosis(incidentId, { autoLoad = true } = {}) {
  const runs = ref([]);
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
      const page = await fetchDiagnosisRuns(id, { signal: controller.signal });
      runs.value = page.items ?? [];
      const runId = preferredRunId || runs.value[0]?.id;
      if (!runId) {
        detail.value = null;
        state.value = "empty";
        return true;
      }
      await loadDetail(runId, controller.signal);
      scheduleRefresh();
      return true;
    } catch (loadError) {
      if (loadError?.name === "AbortError") return false;
      detail.value = null;
      state.value = "error";
      error.value = safeMessage(loadError, "智能分析暂时无法读取");
      return false;
    }
  }

  async function loadDetail(runId, signal) {
    const id = unref(incidentId);
    const response = await fetchDiagnosisRun(id, runId, { signal });
    detail.value = response;
    state.value = "ready";
  }

  async function requestRun(evidenceRunId) {
    const id = unref(incidentId);
    if (!id || !evidenceRunId || mutationState.value === "pending") return false;
    mutationState.value = "pending";
    mutationError.value = "";
    try {
      const response = await createDiagnosisRun(id, evidenceRunId, operationKey());
      await load(response.run.id);
      mutationState.value = "succeeded";
      return true;
    } catch (requestError) {
      mutationState.value = "error";
      mutationError.value = safeMessage(requestError, "智能分析未能启动");
      return false;
    }
  }

  function scheduleRefresh() {
    clearTimeout(pollTimer);
    if (!autoLoad || !activeStates.has(detail.value?.run?.state)) return;
    pollTimer = setTimeout(() => load(detail.value?.run?.id), 2000);
  }

  if (autoLoad) {
    watch(() => unref(incidentId), () => load(), { immediate: true });
    onBeforeUnmount(() => { controller?.abort(); clearTimeout(pollTimer); });
  }

  return { runs, detail, state, error, mutationState, mutationError, load, requestRun };
}
