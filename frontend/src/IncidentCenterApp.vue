<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from "vue";
import {
  PhBell, PhBuildings, PhCaretDown, PhCaretLeft, PhCheckCircle, PhCircle,
  PhCloud, PhGear, PhHeartbeat, PhLinkSimple, PhMagnifyingGlass, PhMonitor,
  PhPulse, PhQuestion, PhShieldCheck, PhSquaresFour, PhUser, PhUsersThree,
} from "@phosphor-icons/vue";

import IncidentActivityTimeline from "./components/IncidentActivityTimeline.vue";
import IncidentOperationDialog from "./components/IncidentOperationDialog.vue";
import IncidentQuickNote from "./components/IncidentQuickNote.vue";
import IncidentResolveDialog from "./components/IncidentResolveDialog.vue";
import IncidentStageBar from "./components/IncidentStageBar.vue";
import AlertCenter from "./components/AlertCenter.vue";
import AlertSourceManager from "./components/AlertSourceManager.vue";
import { useIncidentCenter } from "./composables/useIncidentCenter";

const navItems = [
  { id: "incidents", label: "事故中心", icon: PhPulse },
  { id: "alerts", label: "告警中心", icon: PhBell },
  { id: "alert-sources", label: "接入源管理", icon: PhGear },
  { id: "catalog", label: "服务目录", icon: PhSquaresFour },
  { id: "jobs", label: "关联任务", icon: PhLinkSimple },
  { id: "status", label: "平台状态", icon: PhMonitor },
];
const {
  environment, search, incidents, selectedId, selectedIncident, activeIncidents, resolvedIncidents,
  listState, detailState, listError, detailError, operationState, operationError,
  retryableOperation, loadList, loadDetail, selectIncident: selectFromServer,
  executeSelectedAction, retryLastAction, refreshAfterConflict,
} = useIncidentCenter();
const alertsFocused = ref(false);
const activeNav = ref("incidents");
const notice = ref("");
const resolveOpen = ref(false);
const operationDialog = ref({ open: false, action: "", title: "", targetState: null });
const now = ref(new Date());
const clockTimer = window.setInterval(() => (now.value = new Date()), 1000);
onBeforeUnmount(() => window.clearInterval(clockTimer));

const clockText = computed(() => new Intl.DateTimeFormat("zh-CN", {
  year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  second: "2-digit", hour12: false,
}).format(now.value).replaceAll("/", "-"));
const pageTitle = computed(() => navItems.find((item) => item.id === activeNav.value)?.label ?? "事故智能中心");
const isOperating = computed(() => operationState.value === "pending");
const isClaimed = computed(() => Boolean(selectedIncident.value?.claimedAt));
const can = (action) => selectedIncident.value?.allowedActionCodes.includes(action) ?? false;

function selectIncident(id) {
  alertsFocused.value = false;
  resolveOpen.value = false;
  operationDialog.value.open = false;
  selectFromServer(id);
}
function showNotice(message) {
  notice.value = message;
  window.setTimeout(() => (notice.value = ""), 2200);
}
async function submitOperation({ action, payload }) {
  const success = await executeSelectedAction(action, payload);
  if (!success) return;
  resolveOpen.value = false;
  operationDialog.value.open = false;
  showNotice("操作已保存，事故信息已更新");
}
function openSimpleOperation(action, title, targetState = null) {
  operationDialog.value = { open: true, action, title, targetState };
}
function handlePrimary(primaryAction) {
  if (primaryAction.action === "TRANSITION") {
    openSimpleOperation("transitions", primaryAction.label, primaryAction.targetState);
  } else if (primaryAction.action === "RESOLVE") {
    resolveOpen.value = true;
  } else if (primaryAction.action === "CLOSE") {
    openSimpleOperation("close", "关闭事故");
  }
}
async function focusAlerts() {
  alertsFocused.value = true;
  await nextTick();
  document.querySelector("#related-alerts")?.scrollIntoView({ behavior: "smooth", block: "center" });
}
function chooseNav(item) {
  if (["incidents", "alerts", "alert-sources"].includes(item.id)) {
    activeNav.value = item.id;
    return;
  }
  showNotice(`${item.label}将在后续页面中完善`);
}
function openIncident(incidentId) {
  activeNav.value = "incidents";
  selectIncident(incidentId);
}
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar" aria-label="主导航">
      <div class="brand"><PhShieldCheck :size="23" weight="fill" /><span>事故智能中心</span></div>
      <nav class="nav-list"><button v-for="item in navItems" :key="item.id" type="button" :data-testid="`nav-${item.id}`" :aria-current="activeNav === item.id ? 'page' : undefined" :class="['nav-item', { active: activeNav === item.id }]" @click="chooseNav(item)"><component :is="item.icon" :size="21" /><span>{{ item.label }}</span></button></nav>
      <div class="sidebar-footer"><button type="button" class="nav-item muted" @click="showNotice('系统设置将在后续阶段完善')"><PhGear :size="20" /><span>系统设置</span></button><button type="button" class="nav-item muted" @click="showNotice('导航已收起')"><PhCaretLeft :size="20" /><span>收起导航</span></button></div>
    </aside>

    <main class="main-surface">
      <header class="topbar">
        <div class="title-group"><h1>{{ pageTitle }}</h1><div v-if="activeNav === 'incidents'" class="environment-control"><select v-model="environment" aria-label="环境筛选"><option value="all">全部环境</option><option value="production">生产环境</option><option value="staging">预发环境</option></select><PhCaretDown :size="14" /></div></div>
        <label v-if="activeNav === 'incidents'" class="search-box"><PhMagnifyingGlass :size="18" /><input v-model="search" aria-label="搜索事故、服务或团队" placeholder="搜索事故、服务或团队" /></label>
        <div class="topbar-meta"><time :datetime="now.toISOString()">{{ clockText }}</time><button type="button" class="avatar-button" aria-label="用户菜单" @click="showNotice('当前使用本地控制面身份')"><span>我</span><PhCaretDown :size="13" /></button></div>
      </header>

      <div v-if="activeNav === 'incidents'" class="workspace">
        <section class="incident-queue" aria-label="事故列表">
          <div v-if="listState === 'loading'" class="empty-state"><PhPulse :size="24" /><strong>正在读取事故</strong><span>数据来自事故中心后端</span></div>
          <div v-else-if="listState === 'error'" class="empty-state error-state"><PhQuestion :size="24" /><strong>{{ listError }}</strong><button type="button" data-testid="retry-list" class="button secondary" @click="loadList">重新加载</button></div>
          <div v-else-if="listState === 'empty'" class="empty-state"><PhCheckCircle :size="24" /><strong>当前没有事故</strong><span>这里不会使用演示数据填充</span></div>
          <template v-else>
            <section v-if="activeIncidents.length" class="queue-group"><div class="queue-heading"><PhCaretDown :size="15" /><span>进行中（{{ activeIncidents.length }}）</span></div>
              <article v-for="incident in activeIncidents" :key="incident.id" :data-testid="`incident-${incident.id}`" :class="['incident-row', { selected: selectedId === incident.id }]"><button type="button" @click="selectIncident(incident.id)"><span class="row-title-line"><PhCircle :size="9" weight="fill" :class="`dot-${incident.severityTone}`" /><strong>{{ incident.title }}</strong><time>{{ incident.queueTime }}</time></span><span class="row-badges"><span :class="['badge', `badge-${incident.severityTone}`]">{{ incident.severity }}</span><span class="badge badge-active">{{ incident.state }}</span></span><span class="row-service">{{ incident.service }}</span><span class="row-meta">持续 {{ incident.duration }} · 关联 {{ incident.alertCount }} 条告警</span></button></article>
            </section>
            <section v-if="resolvedIncidents.length" class="queue-group resolved-group"><div class="queue-heading"><PhCaretDown :size="15" /><span>已解决或关闭（{{ resolvedIncidents.length }}）</span></div>
              <article v-for="incident in resolvedIncidents" :key="incident.id" :data-testid="`incident-${incident.id}`" :class="['incident-row', { selected: selectedId === incident.id }]"><button type="button" @click="selectIncident(incident.id)"><span class="row-title-line"><PhCheckCircle :size="14" weight="fill" class="dot-resolved" /><strong>{{ incident.title }}</strong></span><span class="row-badges"><span class="badge badge-resolved">{{ incident.state }}</span></span><span class="row-service">{{ incident.service }}</span><span class="row-meta">持续 {{ incident.duration }} · 关联 {{ incident.alertCount }} 条告警</span></button></article>
            </section><p class="queue-total">共 {{ incidents.length }} 条事故</p>
          </template>
        </section>

        <section class="incident-detail" aria-live="polite">
          <div v-if="detailState === 'loading'" class="detail-placeholder"><PhPulse :size="28" /><strong>正在读取事故详情</strong></div>
          <div v-else-if="detailState === 'error'" class="detail-placeholder error-state"><PhQuestion :size="28" /><strong>{{ detailError }}</strong><button type="button" class="button secondary" @click="loadDetail(selectedId)">重新加载详情</button></div>
          <div v-else-if="!selectedIncident" class="detail-placeholder"><PhPulse :size="28" /><strong>选择事故后查看详情</strong></div>
          <template v-else>
            <header class="detail-header"><div class="detail-title-copy"><div class="detail-title-line"><h2 data-testid="incident-title">{{ selectedIncident.title }}</h2><span :class="['badge', `badge-${selectedIncident.severityTone}`]">{{ selectedIncident.severity }}</span><span :class="['badge', `badge-${selectedIncident.stateTone}`]">{{ selectedIncident.state }}</span></div><p>{{ selectedIncident.impact }}</p></div>
              <div v-if="selectedIncident.allowedActionCodes.length" class="detail-actions" data-testid="incident-write-actions">
                <button v-if="can('CLAIM') && !isClaimed" type="button" data-testid="claim-incident" class="button primary" :disabled="isOperating" @click="submitOperation({ action: 'claim', payload: {} })"><PhUser :size="17" />{{ isOperating ? "保存中…" : "认领事故" }}</button>
                <button v-else-if="isClaimed" type="button" data-testid="claim-incident" class="button primary claimed" disabled><PhUser :size="17" />已认领</button>
                <button v-if="can('RELEASE')" type="button" data-testid="release-incident" class="button secondary" :disabled="isOperating" @click="submitOperation({ action: 'release', payload: {} })">解除认领</button>
                <button v-if="can('RESOLVE')" type="button" data-testid="resolve-incident" class="button secondary" :disabled="isOperating" @click="resolveOpen = true">解决事故</button>
                <button v-if="can('REOPEN')" type="button" data-testid="reopen-incident" class="button primary" :disabled="isOperating" @click="openSimpleOperation('reopen', '重新打开事故')">重新打开</button>
                <button v-if="can('CLOSE')" type="button" data-testid="close-incident" class="button secondary" :disabled="isOperating" @click="openSimpleOperation('close', '关闭事故')">关闭事故</button>
              </div>
            </header>

            <IncidentStageBar :current-state="selectedIncident.stateCode" :primary-action="selectedIncident.primaryAction" :disabled="isOperating" @primary="handlePrimary" />
            <div v-if="operationError" class="operation-error-banner" data-testid="operation-error" role="alert"><div><PhQuestion :size="19" /><span>{{ operationError }}</span></div><button v-if="operationState === 'conflict'" type="button" class="button secondary" data-testid="refresh-conflict" @click="refreshAfterConflict">刷新事故</button><button v-else-if="retryableOperation" type="button" class="button secondary" data-testid="retry-operation" @click="retryLastAction">重试本次操作</button></div>

            <dl class="incident-facts"><div><PhBuildings :size="19" /><dt>所属服务</dt><dd>{{ selectedIncident.service }}</dd></div><div><PhUsersThree :size="19" /><dt>所属团队</dt><dd>{{ selectedIncident.team }}</dd></div><div><PhUser :size="19" /><dt>负责人</dt><dd data-testid="incident-owner">{{ selectedIncident.owner }}</dd></div><div><PhHeartbeat :size="19" /><dt>发现时间</dt><dd>{{ selectedIncident.detectedAt }}</dd></div></dl>
            <div class="detail-body operations-layout"><div class="detail-main-column">
              <IncidentActivityTimeline :activities="selectedIncident.activities" :truncated="selectedIncident.activitiesTruncated" />
              <section class="correlation-explanation"><div class="section-title"><PhQuestion :size="20" weight="bold" /><h3>为什么归为同一事故</h3><span v-if="selectedIncident.ruleVersion" class="rule-confirmed"><PhCheckCircle :size="14" weight="fill" />规则确认</span></div><p>{{ selectedIncident.reason }}</p></section>
              <section id="related-alerts" :class="['alerts-section', { focused: alertsFocused }]"><div class="content-heading"><h3>关联告警（{{ selectedIncident.alerts.length }}）</h3><button type="button" class="link-button" @click="focusAlerts">查看告警</button></div><div class="alerts-table" role="table" aria-label="关联告警"><div class="alerts-head" role="row"><span>告警名称</span><span>状态</span><span>首次出现</span><span>持续时间</span><span>来源</span></div><div v-for="alert in selectedIncident.alerts" :key="alert.id" data-testid="related-alert-row" class="alert-row" role="row"><strong>{{ alert.name }}</strong><span :class="['alert-state', { resolved: alert.state === '已恢复' }]"><PhCircle :size="8" weight="fill" />{{ alert.state }}</span><span>{{ alert.firstSeen }}</span><span>{{ alert.duration }}</span><span class="source-label"><PhCloud v-if="alert.sourceType === 'cloud'" :size="17" /><PhPulse v-else :size="17" />{{ alert.source }}</span></div></div></section>
            </div><IncidentQuickNote v-if="can('ADD_NOTE')" :disabled="isOperating" :reset-key="selectedIncident.version" @operate="submitOperation" /></div>
          </template>
        </section>
      </div>
      <AlertCenter v-else-if="activeNav === 'alerts'" @open-incident="openIncident" />
      <AlertSourceManager v-else-if="activeNav === 'alert-sources'" />
    </main>

    <IncidentResolveDialog :open="resolveOpen" :disabled="isOperating" @close="resolveOpen = false" @operate="submitOperation" />
    <IncidentOperationDialog :open="operationDialog.open" :action="operationDialog.action" :title="operationDialog.title" :target-state="operationDialog.targetState" :disabled="isOperating" @close="operationDialog.open = false" @operate="submitOperation" />
    <Transition name="toast"><div v-if="notice" class="toast" role="status"><PhCheckCircle :size="18" weight="fill" />{{ notice }}</div></Transition>
  </div>
</template>
