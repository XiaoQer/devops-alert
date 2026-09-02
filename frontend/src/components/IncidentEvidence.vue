<script setup>
import { computed, toRef } from "vue";
import { PhArrowsClockwise, PhChartLine, PhDatabase, PhPulse } from "@phosphor-icons/vue";

import { useIncidentEvidence } from "../composables/useIncidentEvidence";
import { evidenceSourceLabels, sourceStatus } from "../presentation/evidenceView";
import EvidenceFinding from "./EvidenceFinding.vue";

const props = defineProps({
  incidentId: { type: String, required: true },
  providedState: { type: Object, default: null },
});
const evidence = props.providedState || useIncidentEvidence(toRef(props, "incidentId"));
const orderedItems = computed(() => [...(evidence.detail.value?.items ?? [])].sort((a, b) => Number(b.needsAttention) - Number(a.needsAttention)));
const firstAttentionIndex = computed(() => orderedItems.value.findIndex((item) => item.needsAttention));
const sources = computed(() => ["PROMETHEUS", "ELASTICSEARCH", "SKYWALKING"].map((type) => {
  const status = sourceStatus(evidence.detail.value?.items ?? [], type);
  return { type, name: evidenceSourceLabels[type], statusLabel: status.label, tone: status.tone };
}));
const packageLabels = { "common-service": "通用服务", http: "HTTP 请求", jvm: "JVM", mysql: "MySQL" };

function chooseRun(event) { evidence.selectRun(event.target.value); }
</script>

<template>
  <section class="incident-evidence">
    <header class="incident-evidence-header">
      <div><PhPulse :size="18" /><div><h3>监控取证</h3><p>平台按 Incident 的环境、服务和告警类型自动收集只读证据。</p></div></div>
      <div class="incident-evidence-actions">
        <select v-if="evidence.runs.value.length" :value="evidence.selectedRunId.value" aria-label="取证历史" @change="chooseRun">
          <option v-for="run in evidence.runs.value" :key="run.id" :value="run.id">{{ run.createdAtLabel }} · {{ run.triggerLabel || run.stateLabel }}</option>
        </select>
        <button type="button" class="button secondary" :disabled="evidence.mutationState.value === 'pending' || evidence.detail.value?.run?.isActive" @click="evidence.requestNewRun"><PhArrowsClockwise :size="15" />重新取证</button>
      </div>
    </header>

    <p v-if="evidence.mutationError.value" class="incident-operation-message">{{ evidence.mutationError.value }}</p>
    <div v-if="evidence.state.value === 'loading'" class="evidence-page-state"><PhArrowsClockwise :size="20" />正在读取取证结果</div>
    <div v-else-if="evidence.state.value === 'error'" class="evidence-page-state error"><span>{{ evidence.error.value }}</span><button type="button" class="button secondary" @click="evidence.load">重新加载</button></div>
    <div v-else-if="evidence.state.value === 'empty'" class="evidence-page-state"><PhDatabase :size="22" /><strong>还没有监控取证</strong><span>可点击“重新取证”启动第一次采集。</span></div>

    <div v-else-if="evidence.detail.value" class="incident-evidence-layout">
      <main>
        <div class="evidence-run-summary">
          <div><span :class="['evidence-run-state', evidence.detail.value.run.state.toLowerCase()]">{{ evidence.detail.value.run.stateLabel }}</span><strong>{{ evidence.detail.value.run.triggerLabel }}</strong></div>
          <p>取得 {{ evidence.detail.value.run.succeeded_count }} 项 · 缺失 {{ evidence.detail.value.run.missing_count + evidence.detail.value.run.skipped_count }} 项 · 失败 {{ evidence.detail.value.run.failed_count }} 项</p>
        </div>
        <div class="evidence-findings-heading"><PhChartLine :size="17" /><h4>取证结果</h4><span>{{ orderedItems.length }} 项</span></div>
        <div v-if="orderedItems.length" class="evidence-findings">
          <EvidenceFinding v-for="(item, index) in orderedItems" :key="item.id || item.evidence_key" :item="item" :open="firstAttentionIndex >= 0 ? index === firstAttentionIndex : index === 0" />
        </div>
        <p v-else class="incident-column-empty">取证任务已创建，证据正在收集中。</p>
      </main>

      <aside class="evidence-run-aside">
        <section><h4>数据来源</h4><div v-for="source in sources" :key="source.type" class="evidence-source-row"><span>{{ source.name }}</span><strong :class="source.tone">{{ source.name === 'SkyWalking' && source.tone === 'attention' ? '查询失败' : source.statusLabel }}</strong></div></section>
        <section><h4>时间范围</h4><p>{{ evidence.detail.value.run.createdAtLabel }} 发起</p><small>{{ evidence.detail.value.run.completedAtLabel }} 完成</small></section>
        <section><h4>取证范围</h4><div class="evidence-pack-list"><span v-for="(_, pack) in evidence.detail.value.run.package_versions" :key="pack">{{ packageLabels[pack] || pack }}</span></div></section>
      </aside>
    </div>
  </section>
</template>
