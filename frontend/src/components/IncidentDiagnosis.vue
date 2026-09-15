<script setup>
import { computed, ref, toRef, watch } from "vue";
import { PhBrain, PhPlay, PhWarningCircle } from "@phosphor-icons/vue";

import { useIncidentEvidence } from "../composables/useIncidentEvidence";
import { useIncidentDiagnosis } from "../composables/useIncidentDiagnosis";

const props = defineProps({
  incidentId: { type: String, required: true },
  evidenceRuns: { type: Array, default: null },
  diagnosis: { type: Object, default: null },
  onRequestRun: { type: Function, default: null },
});
const internalEvidence = props.evidenceRuns === null ? useIncidentEvidence(toRef(props, "incidentId")) : null;
const internalDiagnosis = props.diagnosis === null ? useIncidentDiagnosis(toRef(props, "incidentId")) : null;
const runs = computed(() => props.evidenceRuns ?? internalEvidence.runs.value);
const diagnosis = computed(() => props.diagnosis ?? {
  runs: internalDiagnosis.runs.value,
  detail: internalDiagnosis.detail.value,
  state: internalDiagnosis.state.value,
  error: internalDiagnosis.error.value,
  mutationState: internalDiagnosis.mutationState.value,
  mutationError: internalDiagnosis.mutationError.value,
});
const eligibleRuns = computed(() => runs.value.filter((run) => ["SUCCEEDED", "PARTIAL"].includes(run.state)));
const selectedEvidenceRunId = ref("");
watch(eligibleRuns, (nextRuns) => {
  if (!nextRuns.some((run) => run.id === selectedEvidenceRunId.value)) {
    selectedEvidenceRunId.value = nextRuns[0]?.id ?? "";
  }
}, { immediate: true });
const report = computed(() => diagnosis.value.detail?.report ?? null);
const activeRun = computed(() => diagnosis.value.detail?.run ?? diagnosis.value.runs?.[0] ?? null);
const stateLabel = computed(() => ({ QUEUED: "等待执行", RUNNING: "正在分析", REPORT_READY: "已生成可信报告", REVIEW_REQUIRED: "需要人工复核", FAILED: "分析未完成" }[activeRun.value?.state] ?? "尚未开始"));
const executionStage = computed(() => activeRun.value?.state === "REPORT_READY" ? "已完成" : activeRun.value?.state === "RUNNING" ? "执行中" : "等待启动");

function start() {
  const evidenceRunId = selectedEvidenceRunId.value;
  if (!evidenceRunId) return;
  if (props.onRequestRun) props.onRequestRun(evidenceRunId);
  else internalDiagnosis.requestRun(evidenceRunId);
}
</script>

<template>
  <section class="incident-diagnosis">
    <header class="incident-diagnosis-header">
      <div><PhBrain :size="18" /><div><h3>智能分析</h3><p>平台控制诊断任务；本地演示会仿真 Dify 的固定工作流与受控工具调用。</p></div></div>
      <span :class="['diagnosis-state', activeRun?.state?.toLowerCase()]">{{ stateLabel }}</span>
    </header>

    <section class="diagnosis-executor" aria-label="Dify 执行过程">
      <header><div><strong>Dify Workflow</strong><span>本地 Dify 仿真</span></div><small>固定工作流 · incident-diagnosis.v1</small></header>
      <ol>
        <li><b>1</b><div><strong>平台控制器</strong><span>冻结 Incident、告警和取证引用</span></div><em>{{ executionStage }}</em></li>
        <li><b>2</b><div><strong>Dify Workflow</strong><span>收到运行编号、短期能力凭证与输出契约</span></div><em>{{ executionStage }}</em></li>
        <li><b>3</b><div><strong>平台受控工具</strong><span>诊断快照 → 证据明细 → 知识检索（暂未接入）</span></div><em>{{ activeRun ? executionStage : '等待启动' }}</em></li>
        <li><b>4</b><div><strong>平台校验器</strong><span>校验证据引用、事实边界和人工建议</span></div><em>{{ activeRun?.state === 'REPORT_READY' ? '可信报告已发布' : '等待输出' }}</em></li>
      </ol>
      <p>当前未连接真实 Dify：不会发送 API Key、能力凭证或监控数据到外部服务。</p>
    </section>

    <div class="diagnosis-start">
      <div><strong>选择一次已完成取证</strong><small>诊断启动后会冻结本次 Incident、告警与证据引用。</small></div>
      <select v-model="selectedEvidenceRunId" :disabled="!eligibleRuns.length || diagnosis.mutationState === 'pending'" aria-label="诊断取证运行">
        <option v-for="run in eligibleRuns" :key="run.id" :value="run.id">{{ run.createdAtLabel || run.created_at }} · {{ run.state === 'PARTIAL' ? '部分证据可用' : '取证完成' }}</option>
      </select>
      <button data-testid="start-diagnosis" type="button" class="button primary" :disabled="!selectedEvidenceRunId || diagnosis.mutationState === 'pending'" @click="start"><PhPlay :size="15" />开始演示分析</button>
    </div>
    <p v-if="!eligibleRuns.length" class="diagnosis-hint"><PhWarningCircle :size="15" />请先在“监控取证”完成一次取证，再启动分析。</p>
    <p v-if="diagnosis.mutationError || diagnosis.error" class="incident-operation-message">{{ diagnosis.mutationError || diagnosis.error }}</p>

    <div v-if="report" class="diagnosis-report">
      <section><h4>已确认事实</h4><p v-if="!report.confirmed_facts?.length">暂无可确认事实</p><ul v-else><li v-for="(fact, index) in report.confirmed_facts" :key="index">{{ fact.text || fact }}</li></ul></section>
      <section><h4>待验证假设</h4><p v-if="!report.hypotheses?.length">暂无待验证假设</p><ul v-else><li v-for="(item, index) in report.hypotheses" :key="index">{{ item.text || item }}</li></ul></section>
      <section><h4>建议人工下一步</h4><p v-if="!report.suggested_human_actions?.length">暂无建议</p><ul v-else><li v-for="(action, index) in report.suggested_human_actions" :key="index">{{ action }}</li></ul></section>
    </div>
    <p v-else-if="activeRun?.state === 'REVIEW_REQUIRED'" class="diagnosis-hint">本次输出未通过平台事实与引用校验，未发布为可信报告。</p>
    <p v-else-if="activeRun?.state === 'FAILED'" class="diagnosis-hint">本次演示未完成；不影响 Incident、取证和人工处置。</p>
    <p v-else-if="diagnosis.state === 'empty'" class="diagnosis-hint">尚未启动智能分析。</p>
  </section>
</template>
