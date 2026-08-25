<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from "vue";
import {
  PhBell, PhBuildings, PhCaretDown, PhCaretLeft, PhCheckCircle, PhCircle,
  PhCloud, PhGear, PhHeartbeat, PhLinkSimple, PhMagnifyingGlass, PhMonitor,
  PhPulse, PhQuestion, PhShieldCheck, PhSquaresFour, PhUser, PhUsersThree, PhWrench,
} from "@phosphor-icons/vue";

import { useIncidentCenter } from "./composables/useIncidentCenter";

const navItems = [
  { id: "incidents", label: "事故中心", icon: PhPulse }, { id: "alerts", label: "告警", icon: PhBell },
  { id: "catalog", label: "服务目录", icon: PhSquaresFour }, { id: "jobs", label: "关联任务", icon: PhLinkSimple },
  { id: "status", label: "平台状态", icon: PhMonitor },
];
const {
  environment, search, incidents, selectedId, selectedIncident, activeIncidents, resolvedIncidents,
  listState, detailState, listError, detailError, claimPending, loadList, loadDetail,
  selectIncident: selectFromServer, claimSelected,
} = useIncidentCenter();
const technicalOpen = ref(false);
const alertsFocused = ref(false);
const activeNav = ref("incidents");
const menuOpen = ref(false);
const notice = ref("");
const now = ref(new Date());
const clockTimer = window.setInterval(() => (now.value = new Date()), 1000);
onBeforeUnmount(() => window.clearInterval(clockTimer));

const clockText = computed(() => new Intl.DateTimeFormat("zh-CN", {
  year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
}).format(now.value).replaceAll("/", "-"));
const ownerText = computed(() => selectedIncident.value?.owner === "manual-api-client" ? "当前操作员" : selectedIncident.value?.owner);
const isClaimed = computed(() => Boolean(selectedIncident.value?.claimedAt));

function selectIncident(id) { technicalOpen.value = false; alertsFocused.value = false; selectFromServer(id); }
function showNotice(message) { notice.value = message; window.setTimeout(() => (notice.value = ""), 2200); }
async function claim() {
  const success = await claimSelected();
  if (success) showNotice("事故已由当前操作员认领");
}
async function focusAlerts() {
  alertsFocused.value = true; await nextTick();
  document.querySelector("#related-alerts")?.scrollIntoView({ behavior: "smooth", block: "center" });
}
function chooseNav(item) {
  if (item.id === "incidents") activeNav.value = item.id;
  showNotice(item.id === "incidents" ? "已返回事故中心" : `${item.label}将在后续页面中完善`);
}
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar" aria-label="主导航">
      <div class="brand"><PhShieldCheck :size="23" weight="fill" /><span>事故智能中心</span></div>
      <nav class="nav-list"><button v-for="item in navItems" :key="item.id" type="button" :class="['nav-item', { active: activeNav === item.id }]" @click="chooseNav(item)"><component :is="item.icon" :size="21" /><span>{{ item.label }}</span></button></nav>
      <div class="sidebar-footer"><button type="button" class="nav-item muted" @click="showNotice('系统设置将在后续阶段完善')"><PhGear :size="20" /><span>系统设置</span></button><button type="button" class="nav-item muted" @click="showNotice('导航已收起')"><PhCaretLeft :size="20" /><span>收起导航</span></button></div>
    </aside>

    <main class="main-surface">
      <header class="topbar">
        <div class="title-group"><h1>事故中心</h1><div class="environment-control"><select v-model="environment" aria-label="环境筛选"><option value="all">全部环境</option><option value="production">生产环境</option><option value="staging">预发环境</option></select><PhCaretDown :size="14" /></div></div>
        <label class="search-box"><PhMagnifyingGlass :size="18" /><input v-model="search" aria-label="搜索事故、服务或团队" placeholder="搜索事故、服务或团队" /></label>
        <div class="topbar-meta"><time :datetime="now.toISOString()">{{ clockText }}</time><button type="button" class="avatar-button" aria-label="用户菜单" @click="showNotice('当前使用本地控制面身份')"><span>我</span><PhCaretDown :size="13" /></button></div>
      </header>

      <div class="workspace">
        <section class="incident-queue" aria-label="事故列表">
          <div v-if="listState === 'loading'" class="empty-state"><PhPulse :size="24" /><strong>正在读取事故</strong><span>数据来自事故中心后端</span></div>
          <div v-else-if="listState === 'error'" class="empty-state error-state"><PhQuestion :size="24" /><strong>{{ listError }}</strong><button type="button" data-testid="retry-list" class="button secondary" @click="loadList">重新加载</button></div>
          <div v-else-if="listState === 'empty'" class="empty-state"><PhCheckCircle :size="24" /><strong>当前没有事故</strong><span>这里不会使用演示数据填充</span></div>
          <template v-else>
            <section v-if="activeIncidents.length" class="queue-group"><div class="queue-heading"><PhCaretDown :size="15" /><span>进行中（{{ activeIncidents.length }}）</span></div>
              <article v-for="incident in activeIncidents" :key="incident.id" :data-testid="`incident-${incident.id}`" :class="['incident-row', { selected: selectedId === incident.id }]"><button type="button" @click="selectIncident(incident.id)"><span class="row-title-line"><PhCircle :size="9" weight="fill" :class="`dot-${incident.severityTone}`" /><strong>{{ incident.title }}</strong><time>{{ incident.queueTime }}</time></span><span class="row-badges"><span :class="['badge', `badge-${incident.severityTone}`]">{{ incident.severity }}</span><span class="badge badge-active">{{ incident.state }}</span></span><span class="row-service">{{ incident.service }}</span><span class="row-meta">持续 {{ incident.duration }} · 关联 {{ incident.alertCount }} 条告警</span></button></article>
            </section>
            <section v-if="resolvedIncidents.length" class="queue-group resolved-group"><div class="queue-heading"><PhCaretDown :size="15" /><span>已解决（{{ resolvedIncidents.length }}）</span></div>
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
              <div class="detail-actions"><button type="button" data-testid="claim-incident" :class="['button', 'primary', { claimed: isClaimed }]" :disabled="claimPending || isClaimed || selectedIncident.stateTone === 'resolved'" @click="claim"><PhUser :size="17" weight="bold" />{{ claimPending ? "认领中…" : isClaimed ? "已认领" : selectedIncident.stateTone === "resolved" ? "事故已解决" : "认领事故" }}</button><button type="button" class="button secondary" @click="focusAlerts"><PhBell :size="17" />查看全部告警</button><div class="more-menu-wrap"><button type="button" class="button secondary compact" @click="menuOpen = !menuOpen">更多操作 <PhCaretDown :size="14" /></button><div v-if="menuOpen" class="more-menu"><button type="button" @click="showNotice('事故链接已复制'); menuOpen = false">复制事故链接</button></div></div></div>
            </header>

            <dl class="incident-facts"><div><PhBuildings :size="19" /><dt>所属服务</dt><dd>{{ selectedIncident.service }}</dd></div><div><PhUsersThree :size="19" /><dt>所属团队</dt><dd>{{ selectedIncident.team }}</dd></div><div><PhUser :size="19" /><dt>负责人</dt><dd data-testid="incident-owner">{{ ownerText }}</dd></div><div><PhHeartbeat :size="19" /><dt>发现时间</dt><dd>{{ selectedIncident.detectedAt }}</dd></div></dl>
            <div class="detail-body"><div class="detail-main-column">
              <section class="correlation-explanation"><div class="section-title"><PhQuestion :size="20" weight="bold" /><h3>为什么归为同一事故</h3><span v-if="selectedIncident.ruleVersion" class="rule-confirmed"><PhCheckCircle :size="14" weight="fill" />规则确认</span></div><p>{{ selectedIncident.reason }}</p></section>
              <section id="related-alerts" :class="['alerts-section', { focused: alertsFocused }]"><div class="content-heading"><h3>关联告警（{{ selectedIncident.alerts.length }}）</h3><span v-if="selectedIncident.alertsTruncated">仅展示前 100 条</span><span v-else>数据来自持久化告警</span></div><div class="alerts-table" role="table" aria-label="关联告警"><div class="alerts-head" role="row"><span>告警名称</span><span>状态</span><span>首次出现</span><span>持续时间</span><span>来源</span></div><div v-for="alert in selectedIncident.alerts" :key="alert.id" data-testid="related-alert-row" class="alert-row" role="row"><strong>{{ alert.name }}</strong><span :class="['alert-state', { resolved: alert.state === '已恢复' }]"><PhCircle :size="8" weight="fill" />{{ alert.state }}</span><span>{{ alert.firstSeen }}</span><span>{{ alert.duration }}</span><span class="source-label"><PhCloud v-if="alert.sourceType === 'cloud'" :size="17" /><PhPulse v-else :size="17" />{{ alert.source }}</span></div></div></section>
              <section class="technical-section"><button type="button" data-testid="technical-toggle" :aria-expanded="technicalOpen" @click="technicalOpen = !technicalOpen"><span><PhWrench :size="17" />查看技术详情</span><PhCaretDown :size="15" :class="{ rotated: technicalOpen }" /></button><div v-if="technicalOpen" data-testid="technical-content" class="technical-content"><dl><div><dt>关联规则</dt><dd>{{ selectedIncident.ruleVersion || '暂无' }}</dd></div><div><dt>环境</dt><dd>{{ selectedIncident.environment }}</dd></div><div><dt>事故版本</dt><dd>{{ selectedIncident.version }}</dd></div><div><dt>关联告警</dt><dd>{{ selectedIncident.alerts.length }}</dd></div></dl><p>技术信息来自后端聚合视图，不代表根因分析结论。</p></div></section>
            </div><aside class="timeline" aria-label="事件时间线"><h3>事件时间线</h3><ol><li v-for="event in selectedIncident.timeline" :key="event.id" :class="`tone-${event.tone}`"><span class="timeline-marker"><PhCircle :size="13" weight="fill" /></span><time>{{ event.time }}</time><strong>{{ event.title }}</strong><p>{{ event.detail }}</p></li></ol></aside></div>
          </template>
        </section>
      </div>
    </main>
    <Transition name="toast"><div v-if="notice" class="toast" role="status"><PhCheckCircle :size="18" weight="fill" />{{ notice }}</div></Transition>
  </div>
</template>
