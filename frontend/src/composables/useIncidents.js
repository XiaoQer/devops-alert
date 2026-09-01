import { onBeforeUnmount, onMounted, ref } from "vue";

import { acknowledgeIncident, fetchIncident, fetchIncidents, resolveIncident } from "../api/incidents";
import { toIncidentDetailView, toIncidentListItem } from "../presentation/incidentView";

const safeMessage = (error, fallback) => typeof error?.userMessage === "string" ? error.userMessage : fallback;
const operationKey = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

export function useIncidents({ autoLoad = true } = {}) {
  const incidents = ref([]);
  const total = ref(0);
  const filters = ref({ states: ["OPEN", "ACKNOWLEDGED"], environment: "", severity: "", search: "", limit: 50, offset: 0 });
  const listState = ref("loading");
  const listError = ref("");
  const detail = ref(null);
  const selectedIncident = ref(null);
  const detailState = ref("idle");
  const detailError = ref("");
  const resolutionSummary = ref("");
  const operationState = ref("idle");
  const operationError = ref("");
  let listController;
  let detailController;

  async function loadIncidents() {
    listController?.abort();
    listController = new AbortController();
    listState.value = "loading";
    listError.value = "";
    try {
      const response = await fetchIncidents({ ...filters.value }, { signal: listController.signal });
      incidents.value = (response.items ?? []).map(toIncidentListItem);
      total.value = response.total ?? incidents.value.length;
      listState.value = incidents.value.length ? "ready" : "empty";
    } catch (error) {
      if (error?.name === "AbortError") return;
      incidents.value = [];
      total.value = 0;
      listState.value = "error";
      listError.value = safeMessage(error, "Incident 暂时无法读取");
    }
  }

  async function openIncident(incidentId) {
    detailController?.abort();
    detailController = new AbortController();
    detailState.value = "loading";
    detailError.value = "";
    operationState.value = "idle";
    operationError.value = "";
    try {
      const response = await fetchIncident(incidentId, { signal: detailController.signal });
      detail.value = toIncidentDetailView(response);
      selectedIncident.value = detail.value.incident;
      detailState.value = "ready";
      return true;
    } catch (error) {
      if (error?.name === "AbortError") return false;
      detail.value = null;
      selectedIncident.value = null;
      detailState.value = "error";
      detailError.value = safeMessage(error, "Incident 详情暂时无法读取");
      return false;
    }
  }

  function closeIncident() {
    detailController?.abort();
    detail.value = null;
    selectedIncident.value = null;
    detailState.value = "idle";
    detailError.value = "";
    resolutionSummary.value = "";
    operationState.value = "idle";
    operationError.value = "";
  }

  function updateFilters(patch) {
    filters.value = { ...filters.value, ...patch, offset: patch.offset ?? 0 };
  }

  async function acknowledge() {
    if (!selectedIncident.value || operationState.value === "pending") return false;
    return performOperation(
      () => acknowledgeIncident(selectedIncident.value.id, selectedIncident.value.version, operationKey()),
      "确认 Incident 失败",
    );
  }

  async function resolve() {
    const summary = resolutionSummary.value.trim();
    if (!summary) {
      operationState.value = "error";
      operationError.value = "请填写解决说明";
      return false;
    }
    if (summary.length > 2000) {
      operationState.value = "error";
      operationError.value = "解决说明不能超过 2000 字";
      return false;
    }
    if (!selectedIncident.value || operationState.value === "pending") return false;
    return performOperation(
      () => resolveIncident(selectedIncident.value.id, selectedIncident.value.version, summary, operationKey()),
      "解决 Incident 失败",
      true,
    );
  }

  async function performOperation(operation, fallback, clearResolution = false) {
    const incidentId = selectedIncident.value.id;
    operationState.value = "pending";
    operationError.value = "";
    try {
      const response = await operation();
      selectedIncident.value = toIncidentListItem(response.incident);
      if (detail.value) detail.value.incident = selectedIncident.value;
      operationState.value = "succeeded";
      if (clearResolution) resolutionSummary.value = "";
      await loadIncidents();
      return true;
    } catch (error) {
      const conflict = error?.code === "incident_version_conflict";
      operationState.value = conflict ? "conflict" : "error";
      operationError.value = safeMessage(error, fallback);
      if (conflict) await refreshDetailAfterConflict(incidentId);
      return false;
    }
  }

  async function refreshDetailAfterConflict(incidentId) {
    try {
      const response = await fetchIncident(incidentId);
      detail.value = toIncidentDetailView(response);
      selectedIncident.value = detail.value.incident;
      detailState.value = "ready";
    } catch {
      detailState.value = "error";
      detailError.value = "Incident 已更新，但最新详情暂时无法读取";
    }
  }

  if (autoLoad) {
    onMounted(loadIncidents);
    onBeforeUnmount(() => { listController?.abort(); detailController?.abort(); });
  }

  return {
    incidents, total, filters, listState, listError, detail, selectedIncident,
    detailState, detailError, resolutionSummary, operationState, operationError,
    loadIncidents, openIncident, closeIncident, updateFilters, acknowledge, resolve,
  };
}
