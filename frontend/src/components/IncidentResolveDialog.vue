<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
});
const emit = defineEmits(["close", "operate"]);
const category = ref("");
const message = ref("");
const actions = ref("");
const rootCause = ref("");
const canSubmit = computed(() => !props.disabled
  && category.value
  && message.value.trim()
  && actions.value.trim());

watch(() => props.open, (open) => {
  if (!open) {
    category.value = "";
    message.value = "";
    actions.value = "";
    rootCause.value = "";
  }
});

function submit() {
  if (!canSubmit.value) return;
  emit("operate", {
    action: "resolve",
    payload: {
      category: category.value,
      message: message.value.trim(),
      resolution_actions: actions.value.trim(),
      root_cause: rootCause.value.trim() || null,
    },
  });
}
</script>

<template>
  <div v-if="open" class="dialog-backdrop" role="presentation" @click.self="$emit('close')">
    <section class="operation-dialog" role="dialog" aria-modal="true" aria-labelledby="resolve-title">
      <header><div><span>完成处置</span><h3 id="resolve-title">解决事故</h3></div><button type="button" aria-label="关闭解决弹窗" @click="$emit('close')">×</button></header>
      <label>解决分类
        <select v-model="category" aria-label="解决分类" :disabled="disabled">
          <option value="" disabled>请选择</option>
          <option value="RECOVERED">故障恢复</option>
          <option value="FALSE_POSITIVE">误报</option>
          <option value="DUPLICATE">重复告警</option>
          <option value="NO_ACTION">无需处理</option>
          <option value="OTHER">其他</option>
        </select>
      </label>
      <label>解决说明<textarea v-model="message" aria-label="解决说明" maxlength="2000" :disabled="disabled" /></label>
      <label>采取措施<textarea v-model="actions" aria-label="采取措施" maxlength="4000" :disabled="disabled" /></label>
      <label>根因 <small>选填</small><textarea v-model="rootCause" aria-label="根因说明（选填）" maxlength="4000" :disabled="disabled" /></label>
      <footer><button type="button" class="button secondary" :disabled="disabled" @click="$emit('close')">取消</button><button type="button" class="button primary" data-testid="confirm-resolve" :disabled="!canSubmit" @click="submit">确认解决</button></footer>
    </section>
  </div>
</template>
