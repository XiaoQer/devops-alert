import { onBeforeUnmount, onMounted, ref } from "vue";

import {
  copyIncidentRule,
  createIncidentRule,
  deleteIncidentRule,
  disableIncidentRule,
  dryRunIncidentRule,
  fetchIncidentRule,
  fetchIncidentRules,
  publishIncidentRule,
  updateIncidentRule,
} from "../api/incidentRules";
import { cloneRuleData, createEmptyRuleDraft, ruleToDraft, toIncidentRuleListItem } from "../presentation/incidentRuleView";

const safeMessage = (error, fallback) => typeof error?.userMessage === "string" ? error.userMessage : fallback;
const operationKey = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

export function useIncidentRules({ autoLoad = true } = {}) {
  const rules = ref([]);
  const listState = ref("loading");
  const listError = ref("");
  const mode = ref("list");
  const step = ref(1);
  const currentRule = ref(null);
  const draft = ref(createEmptyRuleDraft());
  const dryRunResult = ref(null);
  const operationState = ref("idle");
  const operationError = ref("");
  let listController;
  let detailController;

  async function loadRules() {
    listController?.abort();
    listController = new AbortController();
    listState.value = "loading";
    listError.value = "";
    try {
      const response = await fetchIncidentRules({ limit: 50 }, { signal: listController.signal });
      rules.value = response.items.map(toIncidentRuleListItem);
      listState.value = rules.value.length ? "ready" : "empty";
    } catch (error) {
      if (error?.name === "AbortError") return;
      rules.value = [];
      listState.value = "error";
      listError.value = safeMessage(error, "Incident 规则暂时无法读取");
    }
  }

  function startCreate() {
    currentRule.value = null;
    draft.value = createEmptyRuleDraft();
    dryRunResult.value = null;
    operationState.value = "idle";
    operationError.value = "";
    step.value = 1;
    mode.value = "wizard";
  }

  function openRule(rule) {
    currentRule.value = cloneRuleData(rule);
    draft.value = ruleToDraft(rule);
    dryRunResult.value = null;
    operationState.value = "idle";
    operationError.value = "";
    step.value = 1;
    mode.value = "wizard";
  }

  async function loadRule(ruleId) {
    detailController?.abort();
    detailController = new AbortController();
    try {
      openRule(await fetchIncidentRule(ruleId, { signal: detailController.signal }));
    } catch (error) {
      if (error?.name === "AbortError") return;
      operationError.value = safeMessage(error, "规则详情暂时无法读取");
    }
  }

  function closeWizard() {
    mode.value = "list";
    step.value = 1;
    currentRule.value = null;
    draft.value = createEmptyRuleDraft();
    dryRunResult.value = null;
    operationError.value = "";
  }

  function updateDraft(patch) {
    Object.assign(draft.value, patch);
    dryRunResult.value = null;
  }

  function updateConfig(patch) {
    Object.assign(draft.value.config, patch);
    dryRunResult.value = null;
  }

  async function saveDraft() {
    operationState.value = "pending";
    operationError.value = "";
    try {
      const command = cloneRuleData(draft.value);
      const result = currentRule.value
        ? await updateIncidentRule(
          currentRule.value.id,
          { ...command, expected_version: currentRule.value.version },
          operationKey(),
        )
        : await createIncidentRule(command, operationKey());
      currentRule.value = result.rule;
      draft.value = ruleToDraft(result.rule);
      dryRunResult.value = null;
      operationState.value = "succeeded";
      return true;
    } catch (error) {
      operationState.value = error?.code === "incident_rule_version_conflict" ? "conflict" : "error";
      operationError.value = safeMessage(error, "草稿保存失败，输入内容已保留");
      return false;
    }
  }

  async function runDryRun(historyHours) {
    if (!currentRule.value) return false;
    operationState.value = "pending";
    operationError.value = "";
    try {
      const result = await dryRunIncidentRule(currentRule.value.id, {
        expected_version: currentRule.value.version,
        history_hours: historyHours,
      });
      currentRule.value = result.rule;
      dryRunResult.value = result;
      operationState.value = "succeeded";
      return true;
    } catch (error) {
      operationState.value = error?.code === "incident_rule_version_conflict" ? "conflict" : "error";
      operationError.value = safeMessage(error, "试运行失败，请稍后重试");
      return false;
    }
  }

  async function publish() {
    if (!currentRule.value?.publishable) return false;
    return performMutation(() => publishIncidentRule(currentRule.value.id, currentRule.value.version, operationKey()));
  }

  async function disable() {
    if (!currentRule.value) return false;
    return performMutation(() => disableIncidentRule(currentRule.value.id, currentRule.value.version, operationKey()));
  }

  async function copy(name) {
    if (!currentRule.value) return false;
    return performMutation(() => copyIncidentRule(currentRule.value.id, name, operationKey()));
  }

  async function remove() {
    if (!currentRule.value) return false;
    operationState.value = "pending";
    try {
      await deleteIncidentRule(currentRule.value.id, currentRule.value.version, operationKey());
      await loadRules();
      closeWizard();
      return true;
    } catch (error) {
      operationState.value = error?.code === "incident_rule_version_conflict" ? "conflict" : "error";
      operationError.value = safeMessage(error, "删除草稿失败");
      return false;
    }
  }

  async function performMutation(operation) {
    operationState.value = "pending";
    operationError.value = "";
    try {
      const result = await operation();
      currentRule.value = result.rule;
      draft.value = ruleToDraft(result.rule);
      operationState.value = "succeeded";
      await loadRules();
      return true;
    } catch (error) {
      operationState.value = error?.code === "incident_rule_version_conflict" ? "conflict" : "error";
      operationError.value = safeMessage(error, "规则操作未完成");
      return false;
    }
  }

  if (autoLoad) {
    onMounted(loadRules);
    onBeforeUnmount(() => { listController?.abort(); detailController?.abort(); });
  }
  return {
    rules, listState, listError, mode, step, currentRule, draft, dryRunResult,
    operationState, operationError, loadRules, startCreate, openRule, loadRule,
    closeWizard, updateDraft, updateConfig, saveDraft, runDryRun, publish, disable,
    copy, remove,
  };
}
