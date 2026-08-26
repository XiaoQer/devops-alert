<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  disabled: { type: Boolean, default: false },
  resetKey: { type: [String, Number], default: 0 },
});
const emit = defineEmits(["operate"]);
const category = ref("CURRENT_FINDING");
const message = ref("");
const canSubmit = computed(() => !props.disabled && message.value.trim().length > 0);

watch(() => props.resetKey, () => {
  category.value = "CURRENT_FINDING";
  message.value = "";
});

function submit() {
  if (!canSubmit.value) return;
  emit("operate", {
    action: "notes",
    payload: { category: category.value, message: message.value.trim() },
  });
}
</script>

<template>
  <aside class="quick-note" data-testid="quick-note">
    <div class="content-heading"><h3>快速记录</h3><span>保存后进入处置时间线</span></div>
    <label>记录分类
      <select v-model="category" aria-label="处置记录分类" :disabled="disabled">
        <option value="CURRENT_FINDING">当前发现</option>
        <option value="ACTION_TAKEN">已执行操作</option>
        <option value="ACTION_RESULT">操作结果</option>
        <option value="NEXT_STEP">后续计划</option>
        <option value="GENERAL">普通备注</option>
      </select>
    </label>
    <label>记录内容
      <textarea
        v-model="message"
        aria-label="处置记录内容"
        maxlength="2000"
        placeholder="记录发现、操作结果或下一步计划"
        :disabled="disabled"
      />
    </label>
    <button
      type="button"
      class="button primary"
      data-testid="save-note"
      :disabled="!canSubmit"
      @click="submit"
    >{{ disabled ? "保存中…" : "保存记录" }}</button>
  </aside>
</template>
