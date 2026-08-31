<script setup>
import { onMounted, ref } from "vue";
import { PhArrowsClockwise, PhFileText, PhPlus } from "@phosphor-icons/vue";

import { fetchAlertSources } from "../api/alertSources";
import { useIncidentRules } from "../composables/useIncidentRules";
import IncidentRuleWizard from "./IncidentRuleWizard.vue";

const state = useIncidentRules();
const sources = ref([]);
onMounted(async () => {
  try { sources.value = (await fetchAlertSources({ limit: 100 })).items; } catch { sources.value = []; }
});

async function closeWizard() { state.closeWizard(); await state.loadRules(); }
async function saveAndContinue() { if (await state.saveDraft()) state.step.value = 4; }
async function reloadCurrent() { if (state.currentRule.value) await state.loadRule(state.currentRule.value.id); }
</script>

<template>
  <div data-testid="incident-rule-center" class="incident-rule-page">
    <template v-if="state.mode.value === 'list'">
      <header class="rule-page-header"><div><h2>Incident 规则</h2><p>由你定义什么样的一组 Alert 值得进入 Incident 识别阶段</p></div><button data-testid="create-incident-rule" type="button" class="button primary" @click="state.startCreate"><PhPlus :size="16" />创建规则</button></header>
      <section class="rule-list-surface">
        <div v-if="state.listState.value === 'loading'" class="rule-list-state"><PhArrowsClockwise :size="25" /><strong>正在读取规则</strong></div>
        <div v-else-if="state.listState.value === 'error'" class="rule-list-state error-state"><strong>{{ state.listError.value }}</strong><button type="button" class="button secondary" @click="state.loadRules">重新加载</button></div>
        <div v-else-if="state.listState.value === 'empty'" class="rule-list-state"><PhFileText :size="30" /><strong>还没有 Incident 规则</strong><span>平台不会创建演示规则。你可以从一条真实、可试运行的规则开始。</span><button data-testid="create-incident-rule" type="button" class="button primary" @click="state.startCreate"><PhPlus :size="16" />创建第一条规则</button></div>
        <template v-else><div class="rule-list-head"><span>规则</span><span>状态</span><span>匹配与触发</span><span>最近更新</span></div><button v-for="rule in state.rules.value" :key="rule.id" data-testid="rule-list-item" type="button" class="rule-list-row" @click="state.openRule(rule)"><div><strong>{{ rule.name }}</strong><small>{{ rule.description || "暂无用途说明" }}</small></div><span :class="['rule-state-pill', rule.stateTone]">{{ rule.stateLabel }}</span><p>{{ rule.summary }}</p><time>{{ rule.updatedAt }}</time></button></template>
      </section>
    </template>
    <IncidentRuleWizard v-else :draft="state.draft.value" :current-rule="state.currentRule.value" :step="state.step.value" :dry-run-result="state.dryRunResult.value" :operation-state="state.operationState.value" :operation-error="state.operationError.value" :sources="sources" @close="closeWizard" @step-change="state.step.value = $event" @draft-change="state.updateDraft" @config-change="state.updateConfig" @save="state.saveDraft" @save-and-continue="saveAndContinue" @dry-run="state.runDryRun" @publish="state.publish" @disable="state.disable" @remove="state.remove" @copy="state.copy" @reload="reloadCurrent" />
  </div>
</template>
