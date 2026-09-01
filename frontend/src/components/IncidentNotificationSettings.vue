<script setup>
import { onMounted, reactive, ref } from "vue";
import { PhArrowsClockwise, PhFloppyDisk } from "@phosphor-icons/vue";

import { createIncidentNotificationRoute, fetchIncidentNotificationRoutes, updateIncidentNotificationRoute } from "../api/incidentNotificationRoutes";
import { toNotificationRouteView } from "../presentation/incidentView";

const emit = defineEmits(["close"]);
const routes = ref([]);
const capability = ref(null);
const state = ref("loading");
const error = ref("");
const saving = ref(false);
const draft = reactive({ id: "", environment: "production", chat_id: "", chat_name: "", enabled: true, version: 0 });
const key = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

async function load() {
  state.value = "loading"; error.value = "";
  try {
    const response = await fetchIncidentNotificationRoutes();
    routes.value = response.items.map(toNotificationRouteView);
    capability.value = response.feishu_capability;
    state.value = "ready";
  } catch (reason) { state.value = "error"; error.value = reason?.userMessage ?? "飞书通知配置暂时无法读取"; }
}
function edit(route) { Object.assign(draft, { id: route.id, environment: route.environment, chat_id: route.chat_id, chat_name: route.chat_name, enabled: route.enabled, version: route.version }); }
function reset() { Object.assign(draft, { id: "", environment: "production", chat_id: "", chat_name: "", enabled: true, version: 0 }); }
async function save() {
  if (!draft.environment.trim() || !draft.chat_id.trim() || !draft.chat_name.trim()) { error.value = "请完整填写环境、群名称和 Chat ID"; return; }
  saving.value = true; error.value = "";
  const command = { environment: draft.environment.trim(), chat_id: draft.chat_id.trim(), chat_name: draft.chat_name.trim(), enabled: draft.enabled };
  try {
    if (draft.id) await updateIncidentNotificationRoute(draft.id, command, draft.version, key());
    else await createIncidentNotificationRoute(command, key());
    reset(); await load();
  } catch (reason) { error.value = reason?.userMessage ?? "飞书通知配置未保存"; }
  finally { saving.value = false; }
}
onMounted(load);
</script>

<template>
  <section data-testid="incident-notification-settings" class="incident-settings-page">
    <header><div><h2>飞书事故群</h2><p>Incident 按环境发送到固定事故群，凭据只通过服务端环境变量配置。</p></div><button type="button" class="button secondary" @click="emit('close')">返回 Incident</button></header>
    <div v-if="state === 'loading'" class="incident-settings-state"><PhArrowsClockwise :size="22" />正在读取配置</div>
    <template v-else>
      <div :class="['feishu-capability', { ready: capability?.configured }]"><strong>{{ capability?.configured ? '飞书能力已就绪' : '飞书凭据不完整' }}</strong><span>{{ capability?.configured ? '可以启用环境通知路由' : '请由部署人员补齐服务端飞书环境变量' }}</span></div>
      <p v-if="error" class="incident-operation-message error">{{ error }}</p>
      <div class="incident-settings-grid">
        <section class="incident-route-list"><h3>已配置路由</h3><p v-if="!routes.length" class="incident-column-empty">还没有配置事故群</p><button v-for="route in routes" :key="route.id" type="button" @click="edit(route)"><span><strong>{{ route.chat_name }}</strong><small>{{ route.environmentLabel }}</small></span><span><code>{{ route.chatIdMasked }}</code><small>{{ route.enabledLabel }}</small></span></button></section>
        <form class="incident-route-form" @submit.prevent="save"><h3>{{ draft.id ? '修改通知路由' : '新增通知路由' }}</h3><label>环境<input v-model="draft.environment" maxlength="32" /></label><label>飞书群名称<input v-model="draft.chat_name" maxlength="128" /></label><label>Chat ID<input v-model="draft.chat_id" maxlength="128" placeholder="oc_..." /></label><label class="route-switch"><input v-model="draft.enabled" type="checkbox" />启用此环境的通知</label><div><button v-if="draft.id" type="button" class="button secondary" @click="reset">取消编辑</button><button type="submit" class="button primary" :disabled="saving || !capability?.configured"><PhFloppyDisk :size="16" />保存</button></div></form>
      </div>
    </template>
  </section>
</template>
