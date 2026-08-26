import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import {
  executeIncidentAction,
  fetchIncidentOverview,
  fetchIncidents,
} from "../api/incidents";
import { toIncidentDetail, toIncidentListItem } from "../presentation/incidentView";

function safeMessage(error, fallback) {
  return typeof error?.userMessage === "string" ? error.userMessage : fallback;
}

export function useIncidentCenter() {
  const environment = ref("production");
  const search = ref("");
  const incidents = ref([]);
  const selectedId = ref(null);
  const selectedIncident = ref(null);
  const listState = ref("loading");
  const detailState = ref("idle");
  const listError = ref("");
  const detailError = ref("");
  const operationState = ref("idle");
  const operationError = ref("");
  const retryableOperation = ref(null);
  const claimPending = computed(() => operationState.value === "pending");
  let listController;
  let detailController;
  let operationController;
  let debounceTimer;
  let listSequence = 0;
  let detailSequence = 0;

  const activeIncidents = computed(() => incidents.value.filter((item) => item.stateTone !== "resolved"));
  const resolvedIncidents = computed(() => incidents.value.filter((item) => item.stateTone === "resolved"));

  async function loadDetail(id) {
    if (!id) return;
    detailController?.abort();
    detailController = new AbortController();
    const sequence = ++detailSequence;
    detailState.value = "loading";
    detailError.value = "";
    try {
      const response = await fetchIncidentOverview(id, { signal: detailController.signal });
      if (sequence !== detailSequence) return;
      selectedIncident.value = toIncidentDetail(response);
      detailState.value = "ready";
    } catch (error) {
      if (error?.name === "AbortError" || sequence !== detailSequence) return;
      selectedIncident.value = null;
      detailState.value = "error";
      detailError.value = safeMessage(error, "事故详情暂时不可用，请稍后重试");
    }
  }

  async function loadList() {
    listController?.abort();
    listController = new AbortController();
    const sequence = ++listSequence;
    listState.value = "loading";
    listError.value = "";
    try {
      const response = await fetchIncidents(
        { environment: environment.value, query: search.value },
        { signal: listController.signal },
      );
      if (sequence !== listSequence) return;
      incidents.value = response.items.map((item) => toIncidentListItem(item));
      listState.value = incidents.value.length ? "ready" : "empty";
      if (!incidents.value.length) {
        selectedId.value = null;
        selectedIncident.value = null;
        detailState.value = "idle";
        return;
      }
      if (!incidents.value.some((item) => item.id === selectedId.value)) {
        selectedId.value = incidents.value[0].id;
      }
      await loadDetail(selectedId.value);
    } catch (error) {
      if (error?.name === "AbortError" || sequence !== listSequence) return;
      incidents.value = [];
      selectedId.value = null;
      selectedIncident.value = null;
      listState.value = "error";
      detailState.value = "idle";
      listError.value = safeMessage(error, "事故数据暂时不可用，请稍后重试");
    }
  }

  function selectIncident(id) {
    if (id === selectedId.value && detailState.value === "ready") return;
    selectedId.value = id;
    loadDetail(id);
  }

  async function performOperation(operation) {
    if (operationState.value === "pending") return false;
    operationController = new AbortController();
    operationState.value = "pending";
    operationError.value = "";
    try {
      await executeIncidentAction(
        operation.incidentId,
        operation.action,
        operation.command,
        operation.key,
        { signal: operationController.signal },
      );
      retryableOperation.value = null;
      await loadList();
      operationState.value = "succeeded";
      return true;
    } catch (error) {
      if (error?.name === "AbortError") return false;
      if (error?.code === "incident_api_unavailable") {
        retryableOperation.value = operation;
        operationState.value = "unknown";
        operationError.value = safeMessage(
          error,
          "事故操作结果暂时未知，请重试本次操作",
        );
        return false;
      }
      retryableOperation.value = null;
      if (error?.code === "incident_version_conflict") {
        operationState.value = "conflict";
      } else {
        operationState.value = "error";
      }
      operationError.value = safeMessage(error, "事故操作未完成，请稍后重试");
      return false;
    } finally {
      operationController = undefined;
    }
  }

  async function executeSelectedAction(action, payload = {}) {
    if (!selectedId.value || !selectedIncident.value || operationState.value === "pending") {
      return false;
    }
    const operation = {
      incidentId: selectedId.value,
      action,
      command: { expected_version: selectedIncident.value.version, ...payload },
      key: globalThis.crypto.randomUUID(),
    };
    retryableOperation.value = null;
    return performOperation(operation);
  }

  async function retryLastAction() {
    const operation = retryableOperation.value;
    if (!operation || operationState.value === "pending") return false;
    return performOperation(operation);
  }

  async function refreshAfterConflict() {
    retryableOperation.value = null;
    operationError.value = "";
    operationState.value = "idle";
    await loadList();
  }

  function claimSelected() {
    return executeSelectedAction("claim", {});
  }

  watch([environment, search], () => {
    window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(loadList, 250);
  });
  onMounted(loadList);
  onBeforeUnmount(() => {
    window.clearTimeout(debounceTimer);
    listController?.abort();
    detailController?.abort();
    operationController?.abort();
  });

  return {
    environment, search, incidents, selectedId, selectedIncident, activeIncidents,
    resolvedIncidents, listState, detailState, listError, detailError, claimPending,
    operationState, operationError, retryableOperation,
    loadList, loadDetail, selectIncident, claimSelected, executeSelectedAction,
    retryLastAction, refreshAfterConflict,
  };
}
