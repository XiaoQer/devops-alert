<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  action: { type: String, default: "" },
  title: { type: String, default: "事故操作" },
  targetState: { type: String, default: null },
  disabled: { type: Boolean, default: false },
});
const emit = defineEmits(["close", "operate"]);
const message = ref("");
const canSubmit = computed(() => !props.disabled && message.value.trim());

watch(() => props.open, (open) => { if (!open) message.value = ""; });

function submit() {
  if (!canSubmit.value) return;
  const payload = props.action === "transitions"
    ? { target_state: props.targetState, message: message.value.trim() }
    : props.action === "reopen"
      ? { reason: message.value.trim() }
      : { message: message.value.trim() };
  emit("operate", { action: props.action, payload });
}
</script>

<template>
  <div v-if="open" class="dialog-backdrop" role="presentation" @click.self="$emit('close')">
    <section class="operation-dialog compact-dialog" role="dialog" aria-modal="true">
      <header><h3>{{ title }}</h3><button type="button" aria-label="关闭操作弹窗" @click="$emit('close')">×</button></header>
      <label>操作说明<textarea v-model="message" aria-label="操作说明" maxlength="2000" :disabled="disabled" /></label>
      <footer><button type="button" class="button secondary" :disabled="disabled" @click="$emit('close')">取消</button><button type="button" class="button primary" :disabled="!canSubmit" @click="submit">确认</button></footer>
    </section>
  </div>
</template>
