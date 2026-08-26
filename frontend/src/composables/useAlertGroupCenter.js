import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { fetchAlertGroupMembers, fetchAlertGroupOverview, fetchAlertGroups, fetchAlertGroupSummary } from "../api/alertGroups";
import { toAlertGroupDetail, toAlertGroupListItem, toAlertGroupMember, toAlertGroupSummary } from "../presentation/alertGroupView";

const safeMessage = (error, fallback) => typeof error?.userMessage === "string" ? error.userMessage : fallback;

export function useAlertGroupCenter() {
  const state = ref(""); const severity = ref(""); const environment = ref(""); const storm = ref(""); const linked = ref(""); const search = ref("");
  const groups = ref([]); const summary = ref(null); const selectedId = ref(null); const detail = ref(null); const members = ref([]);
  const total = ref(0); const limit = ref(50); const offset = ref(0);
  const memberTotal = ref(0); const memberLimit = ref(100); const memberOffset = ref(0);
  const listState = ref("loading"); const summaryState = ref("loading"); const detailState = ref("idle"); const memberState = ref("idle");
  const listError = ref(""); const summaryError = ref(""); const detailError = ref(""); const memberError = ref("");
  let listController; let summaryController; let detailController; let memberController; let debounceTimer;
  let listSequence = 0; let detailSequence = 0; let memberSequence = 0;

  async function loadSummary() {
    summaryController?.abort(); summaryController = new AbortController(); summaryState.value = "loading"; summaryError.value = "";
    try { summary.value = toAlertGroupSummary(await fetchAlertGroupSummary("24h", { signal: summaryController.signal })); summaryState.value = "ready"; }
    catch (error) { if (error?.name === "AbortError") return; summary.value = null; summaryState.value = "error"; summaryError.value = safeMessage(error, "告警组概况暂时不可用"); }
  }

  async function loadMembers(id = selectedId.value) {
    if (!id) return;
    memberController?.abort(); memberController = new AbortController(); const sequence = ++memberSequence; memberState.value = "loading"; memberError.value = "";
    try {
      const response = await fetchAlertGroupMembers(id, { limit: memberLimit.value, offset: memberOffset.value }, { signal: memberController.signal });
      if (sequence !== memberSequence || id !== selectedId.value) return;
      members.value = response.items.map(toAlertGroupMember); memberTotal.value = response.total; memberState.value = members.value.length ? "ready" : "empty";
    } catch (error) {
      if (error?.name === "AbortError" || sequence !== memberSequence) return;
      members.value = []; memberTotal.value = 0; memberState.value = "error"; memberError.value = safeMessage(error, "组内原始告警暂时不可用");
    }
  }

  async function loadDetail(id) {
    if (!id) return;
    detailController?.abort(); memberController?.abort(); detailController = new AbortController(); const sequence = ++detailSequence;
    selectedId.value = id; memberOffset.value = 0; detailState.value = "loading"; memberState.value = "loading"; detailError.value = "";
    try {
      const response = await fetchAlertGroupOverview(id, { signal: detailController.signal });
      if (sequence !== detailSequence || id !== selectedId.value) return;
      detail.value = toAlertGroupDetail(response); detailState.value = "ready";
      await loadMembers(id);
    } catch (error) {
      if (error?.name === "AbortError" || sequence !== detailSequence) return;
      detail.value = null; members.value = []; memberTotal.value = 0; detailState.value = "error"; memberState.value = "idle";
      detailError.value = safeMessage(error, "告警组详情暂时不可用");
    }
  }

  async function loadList() {
    listController?.abort(); listController = new AbortController(); const sequence = ++listSequence; listState.value = "loading"; listError.value = "";
    try {
      const response = await fetchAlertGroups({ state: state.value, severity: severity.value, environment: environment.value, storm_state: storm.value, incident_linked: linked.value, query: search.value, limit: limit.value, offset: offset.value }, { signal: listController.signal });
      if (sequence !== listSequence) return;
      groups.value = response.items.map(toAlertGroupListItem); total.value = response.total; listState.value = groups.value.length ? "ready" : "empty";
      if (!groups.value.length) { selectedId.value = null; detail.value = null; members.value = []; detailState.value = "idle"; memberState.value = "idle"; return; }
      const id = groups.value.some((item) => item.id === selectedId.value) ? selectedId.value : groups.value[0].id;
      await loadDetail(id);
    } catch (error) {
      if (error?.name === "AbortError" || sequence !== listSequence) return;
      groups.value = []; total.value = 0; selectedId.value = null; detail.value = null; members.value = []; listState.value = "error"; detailState.value = "idle"; memberState.value = "idle"; listError.value = safeMessage(error, "告警组数据暂时不可用");
    }
  }

  const hasPrevious = computed(() => offset.value > 0); const hasNext = computed(() => offset.value + groups.value.length < total.value);
  const rangeStart = computed(() => groups.value.length ? offset.value + 1 : 0); const rangeEnd = computed(() => offset.value + groups.value.length);
  const memberHasPrevious = computed(() => memberOffset.value > 0); const memberHasNext = computed(() => memberOffset.value + members.value.length < memberTotal.value);
  const memberRangeStart = computed(() => members.value.length ? memberOffset.value + 1 : 0); const memberRangeEnd = computed(() => memberOffset.value + members.value.length);
  async function goPrevious() { if (!hasPrevious.value) return; offset.value = Math.max(0, offset.value - limit.value); await loadList(); }
  async function goNext() { if (!hasNext.value) return; offset.value += limit.value; await loadList(); }
  async function goMemberPrevious() { if (!memberHasPrevious.value) return; memberOffset.value = Math.max(0, memberOffset.value - memberLimit.value); await loadMembers(); }
  async function goMemberNext() { if (!memberHasNext.value) return; memberOffset.value += memberLimit.value; await loadMembers(); }
  watch([state, severity, environment, storm, linked, search], () => { offset.value = 0; window.clearTimeout(debounceTimer); debounceTimer = window.setTimeout(loadList, 250); });
  onMounted(() => { loadSummary(); loadList(); });
  onBeforeUnmount(() => { window.clearTimeout(debounceTimer); listController?.abort(); summaryController?.abort(); detailController?.abort(); memberController?.abort(); });
  return { state, severity, environment, storm, linked, search, groups, summary, selectedId, detail, members, total, memberTotal, listState, summaryState, detailState, memberState, listError, summaryError, detailError, memberError, hasPrevious, hasNext, rangeStart, rangeEnd, memberHasPrevious, memberHasNext, memberRangeStart, memberRangeEnd, loadList, loadSummary, loadDetail, loadMembers, goPrevious, goNext, goMemberPrevious, goMemberNext };
}
