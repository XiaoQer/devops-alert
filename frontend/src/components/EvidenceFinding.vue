<script setup>
import { computed } from "vue";

const props = defineProps({ item: { type: Object, required: true }, open: { type: Boolean, default: false } });

const baseline = computed(() => summaryRows(props.item.baseline_summary));
const fault = computed(() => summaryRows(props.item.fault_summary));

function summaryRows(summary = {}) {
  const labels = { sample_count: "样本数", average: "平均值", maximum: "最大值", median: "中位数", total: "总量", error_count: "错误数" };
  return Object.entries(summary)
    .filter(([, value]) => value !== null && value !== undefined)
    .slice(0, 4)
    .map(([key, value]) => ({ key, label: labels[key] ?? key, value: typeof value === "number" ? Number(value.toFixed(3)) : value }));
}
</script>

<template>
  <details :open="open" :data-testid="item.needsAttention ? 'finding-attention' : 'finding-normal'" :class="['evidence-finding', item.tone]">
    <summary>
      <span class="evidence-finding-marker">{{ item.needsAttention ? '!' : '✓' }}</span>
      <div><strong>{{ item.display_name }}</strong><small>{{ item.sourceLabel }}</small></div>
      <span class="evidence-state-label">{{ item.stateLabel }}</span>
    </summary>
    <div class="evidence-finding-body">
      <p>{{ item.interpretation || '该证据项没有补充说明。' }}</p>
      <div v-if="baseline.length || fault.length" class="evidence-comparison">
        <section><span>故障前</span><dl><div v-for="row in baseline" :key="row.key"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></div></dl></section>
        <section><span>故障期间</span><dl><div v-for="row in fault" :key="row.key"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></div></dl></section>
      </div>
    </div>
  </details>
</template>
