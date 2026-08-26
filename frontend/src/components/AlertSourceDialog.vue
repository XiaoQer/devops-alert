<script setup>
import { ref, watch } from "vue";
const props = defineProps({ open: Boolean, disabled: Boolean }); const emit = defineEmits(["close", "submit"]);
const name = ref(""); const type = ref("ALERTMANAGER");
watch(() => props.open, (open) => { if (open) { name.value = ""; type.value = "ALERTMANAGER"; } });
function submit() { if (name.value.trim()) emit("submit", { name: name.value.trim(), source_type: type.value }); }
</script>
<template><div v-if="open" class="dialog-backdrop"><form class="operation-dialog compact-dialog" @submit.prevent="submit"><header><div><span>新增数据入口</span><h3>创建告警源</h3></div><button type="button" aria-label="关闭" @click="$emit('close')">×</button></header><label>来源名称<input data-testid="source-name" v-model="name" maxlength="128" required placeholder="例如：生产 Prometheus" /></label><label>接入方式<select v-model="type"><option value="ALERTMANAGER">Prometheus / Alertmanager</option><option value="CLOUDEVENTS">CloudEvents</option></select></label><footer><button type="button" class="button secondary" @click="$emit('close')">取消</button><button data-testid="submit-source" type="submit" class="button primary" :disabled="disabled" @click.prevent="submit">{{ disabled ? "创建中…" : "创建来源" }}</button></footer></form></div></template>
