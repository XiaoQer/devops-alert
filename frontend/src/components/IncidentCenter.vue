<script setup>
import { ref } from "vue";
import { PhArrowsClockwise, PhFunnel, PhGear, PhSiren } from "@phosphor-icons/vue";

import { useIncidents } from "../composables/useIncidents";
import IncidentDetail from "./IncidentDetail.vue";
import IncidentNotificationSettings from "./IncidentNotificationSettings.vue";

const state = useIncidents();
const settingsOpen = ref(false);

async function applyFilters() { state.updateFilters({ offset: 0 }); await state.loadIncidents(); }
</script>

<template>
  <div data-testid="incident-center" class="incident-ops-page">
    <IncidentNotificationSettings v-if="settingsOpen" @close="settingsOpen = false" />
    <template v-else>
      <header class="incident-ops-intro"><div><strong>正式 Incident</strong><span>达到已发布规则条件后自动生成，由人员确认和解决。</span></div><button type="button" class="button secondary" @click="settingsOpen = true"><PhGear :size="16" />飞书事故群</button></header>
      <form class="incident-ops-filters" @submit.prevent="applyFilters"><PhFunnel :size="16" /><input v-model="state.filters.value.search" aria-label="搜索 Incident" placeholder="搜索编号、标题或对象" /><select v-model="state.filters.value.environment" aria-label="环境"><option value="">全部环境</option><option value="production">生产环境</option><option value="staging">预发环境</option><option value="testing">测试环境</option></select><select v-model="state.filters.value.severity" aria-label="严重级别"><option value="">全部级别</option><option value="critical">严重</option><option value="high">重要</option><option value="medium">一般</option><option value="low">提示</option></select><button type="submit" class="button secondary">筛选</button></form>
      <div class="incident-ops-workspace">
        <aside class="incident-ops-list" aria-label="Incident 列表">
          <div class="incident-list-caption"><span>未解决 Incident</span><strong>{{ state.total.value }}</strong></div>
          <div v-if="state.listState.value === 'loading'" class="incident-list-state"><PhArrowsClockwise :size="22" />正在读取 Incident</div>
          <div v-else-if="state.listState.value === 'error'" class="incident-list-state error"><span>{{ state.listError.value }}</span><button type="button" class="button secondary" @click="state.loadIncidents">重新加载</button></div>
          <div v-else-if="state.listState.value === 'empty'" class="incident-list-state"><PhSiren :size="26" /><strong>当前没有未解决 Incident</strong><span>已发布规则命中新告警后会自动创建 Incident。</span></div>
          <button v-for="incident in state.incidents.value" v-else :key="incident.id" data-testid="incident-row" type="button" :class="['incident-ops-row', { selected: state.selectedIncident.value?.id === incident.id }]" @click="state.openIncident(incident.id)"><div><span :class="['severity-dot', incident.severity]"></span><strong>{{ incident.reference }}</strong><small>{{ incident.stateLabel }}</small></div><h3>{{ incident.title }}</h3><p>{{ incident.environmentLabel }} · {{ incident.group_display_name }} · {{ incident.alertSummary }}</p><footer><span>{{ incident.rule_name }}</span><time>{{ incident.updatedAtLabel }}</time></footer></button>
        </aside>
        <div v-if="state.detailState.value === 'loading'" class="incident-detail-placeholder"><PhArrowsClockwise :size="24" />正在读取详情</div>
        <div v-else-if="state.detailState.value === 'error'" class="incident-detail-placeholder error">{{ state.detailError.value }}</div>
        <IncidentDetail v-else-if="state.detail.value" :detail="state.detail.value" :operation-state="state.operationState.value" :operation-error="state.operationError.value" :resolution-summary="state.resolutionSummary.value" @close="state.closeIncident" @acknowledge="state.acknowledge" @resolve="state.resolve" @update:resolution-summary="state.resolutionSummary.value = $event" />
        <div v-else class="incident-detail-placeholder"><PhSiren :size="28" /><strong>选择一个 Incident 查看详情</strong><span>这里将展示当前情况、关联告警和完整处置记录。</span></div>
      </div>
    </template>
  </div>
</template>
