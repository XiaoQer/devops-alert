<script setup>
defineProps({
  currentState: { type: String, required: true },
  primaryAction: { type: Object, default: null },
  disabled: { type: Boolean, default: false },
});
defineEmits(["primary"]);

const stages = [
  ["DETECTED", "待处置"],
  ["TRIAGING", "分诊中"],
  ["INVESTIGATING", "调查中"],
  ["MITIGATING", "缓解中"],
  ["MONITORING_RECOVERY", "恢复观察"],
  ["RESOLVED", "已解决"],
  ["CLOSED", "已关闭"],
];
</script>

<template>
  <section class="stage-panel" data-testid="incident-stage-bar" aria-label="事故处置阶段">
    <div class="stage-summary">
      <div>
        <span>处置进度</span>
        <strong>{{ stages.find(([code]) => code === currentState)?.[1] || "未知状态" }}</strong>
      </div>
      <button
        v-if="primaryAction"
        type="button"
        class="button primary"
        data-testid="primary-operation"
        :disabled="disabled"
        @click="$emit('primary', primaryAction)"
      >{{ primaryAction.label }}</button>
    </div>
    <ol class="stage-track">
      <li
        v-for="([code, label], index) in stages"
        :key="code"
        :class="{ current: code === currentState }"
      >
        <span>{{ index + 1 }}</span>
        <small>{{ label }}</small>
      </li>
    </ol>
  </section>
</template>
