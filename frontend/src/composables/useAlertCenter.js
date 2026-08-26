import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { fetchAlertOverview, fetchAlerts, fetchAlertSummary } from "../api/alerts";
import { toAlertDetail, toAlertListItem, toAlertSummary } from "../presentation/alertView";

const safeMessage = (error, fallback) => typeof error?.userMessage === "string" ? error.userMessage : fallback;

export function useAlertCenter() {
  const state = ref(""); const severity = ref(""); const environment = ref(""); const linked = ref(""); const search = ref("");
  const alerts = ref([]); const summary = ref(null); const selectedId = ref(null); const detail = ref(null);
  const total = ref(0); const limit = ref(50); const offset = ref(0);
  const listState = ref("loading"); const summaryState = ref("loading"); const detailState = ref("idle");
  const listError = ref(""); const summaryError = ref(""); const detailError = ref("");
  let listController; let summaryController; let detailController; let debounceTimer; let listSequence = 0; let detailSequence = 0;

  async function loadSummary() {
    summaryController?.abort(); summaryController = new AbortController(); summaryState.value = "loading"; summaryError.value = "";
    try { summary.value = toAlertSummary(await fetchAlertSummary("24h", { signal: summaryController.signal })); summaryState.value = "ready"; }
    catch (error) { if (error?.name === "AbortError") return; summary.value = null; summaryState.value = "error"; summaryError.value = safeMessage(error, "告警概况暂时不可用"); }
  }

  async function loadDetail(id) {
    if (!id) return;
    detailController?.abort(); detailController = new AbortController(); const sequence = ++detailSequence; detailState.value = "loading"; detailError.value = "";
    try { const response = await fetchAlertOverview(id, { signal: detailController.signal }); if (sequence !== detailSequence) return; detail.value = toAlertDetail(response); detailState.value = "ready"; }
    catch (error) { if (error?.name === "AbortError" || sequence !== detailSequence) return; detail.value = null; detailState.value = "error"; detailError.value = safeMessage(error, "告警详情暂时不可用"); }
  }

  async function loadList() {
    listController?.abort(); listController = new AbortController(); const sequence = ++listSequence; listState.value = "loading"; listError.value = "";
    try {
      const response = await fetchAlerts({ state: state.value, severity: severity.value, environment: environment.value, incident_linked: linked.value, query: search.value, limit: limit.value, offset: offset.value }, { signal: listController.signal });
      if (sequence !== listSequence) return;
      alerts.value = response.items.map(toAlertListItem); total.value = response.total; listState.value = alerts.value.length ? "ready" : "empty";
      if (!alerts.value.length) { selectedId.value = null; detail.value = null; detailState.value = "idle"; return; }
      if (!alerts.value.some((item) => item.id === selectedId.value)) selectedId.value = alerts.value[0].id;
      await loadDetail(selectedId.value);
    } catch (error) {
      if (error?.name === "AbortError" || sequence !== listSequence) return;
      alerts.value = []; total.value = 0; selectedId.value = null; detail.value = null; listState.value = "error"; detailState.value = "idle"; listError.value = safeMessage(error, "告警数据暂时不可用");
    }
  }

  async function selectAlert(id) { selectedId.value = id; await loadDetail(id); }
  const hasPrevious = computed(() => offset.value > 0);
  const hasNext = computed(() => offset.value + alerts.value.length < total.value);
  const rangeStart = computed(() => alerts.value.length ? offset.value + 1 : 0);
  const rangeEnd = computed(() => offset.value + alerts.value.length);
  async function goPrevious() { if (!hasPrevious.value) return; offset.value = Math.max(0, offset.value - limit.value); await loadList(); }
  async function goNext() { if (!hasNext.value) return; offset.value += limit.value; await loadList(); }
  watch([state, severity, environment, linked, search], () => { offset.value = 0; window.clearTimeout(debounceTimer); debounceTimer = window.setTimeout(loadList, 250); });
  onMounted(() => { loadSummary(); loadList(); });
  onBeforeUnmount(() => { window.clearTimeout(debounceTimer); listController?.abort(); summaryController?.abort(); detailController?.abort(); });
  return { state, severity, environment, linked, search, alerts, summary, selectedId, detail, total, limit, offset, hasPrevious, hasNext, rangeStart, rangeEnd, listState, summaryState, detailState, listError, summaryError, detailError, loadList, loadSummary, loadDetail, selectAlert, goPrevious, goNext };
}
