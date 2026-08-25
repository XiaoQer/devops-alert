<script setup>
import { computed, nextTick, ref } from "vue";
import {
  PhBell, PhBuildings, PhCaretDown, PhCaretLeft, PhCheckCircle, PhCircle,
  PhCloud, PhGear, PhHeartbeat, PhLinkSimple, PhMagnifyingGlass,
  PhMonitor, PhPulse, PhQuestion, PhShieldCheck, PhSquaresFour, PhUser,
  PhUsersThree, PhWrench,
} from "@phosphor-icons/vue";

const incidents = [
  {
    id: "inc-payment", title: "支付接口错误率持续升高", service: "payment-api",
    team: "支付平台组", owner: "李强", severity: "严重", severityTone: "critical",
    state: "处理中", stateTone: "active", environment: "production",
    detectedAt: "2026-08-25 10:06:18", queueTime: "10:06", duration: "18 分钟",
    impact: "支付成功率下降，影响结算链路，当前仍在持续",
    reason: "3 条告警来自同一生产服务，均发生在 15 分钟窗口内，因此自动关联。",
    alerts: [
      { name: "支付接口 5xx 错误率升高", state: "触发中", firstSeen: "10:06:01", duration: "18 分钟", source: "Prometheus", sourceType: "prometheus" },
      { name: "支付成功率下降", state: "触发中", firstSeen: "10:07:12", duration: "16 分钟", source: "CloudEvents", sourceType: "cloud" },
      { name: "支付接口超时率升高", state: "触发中", firstSeen: "10:09:45", duration: "13 分钟", source: "Prometheus", sourceType: "prometheus" },
    ],
    timeline: [
      { time: "10:06:01", title: "首次告警出现", detail: "支付接口 5xx 错误率升高", tone: "danger" },
      { time: "10:06:18", title: "创建事故", detail: "系统自动创建事故", tone: "neutral" },
      { time: "10:07:12", title: "关联第二条告警", detail: "支付成功率下降", tone: "neutral" },
      { time: "10:09:45", title: "关联第三条告警", detail: "支付接口超时率升高", tone: "neutral" },
      { time: "10:24:18", title: "告警仍在持续", detail: "事故持续处理中", tone: "current" },
    ],
  },
  {
    id: "inc-order", title: "订单服务响应变慢", service: "order-service",
    team: "交易平台组", owner: "王楠", severity: "重要", severityTone: "high",
    state: "处理中", stateTone: "active", environment: "production",
    detectedAt: "2026-08-25 09:47:02", queueTime: "09:47", duration: "37 分钟",
    impact: "订单请求延迟升高，部分用户提交订单变慢",
    reason: "2 条告警来自同一生产服务，并在 15 分钟窗口内连续出现，因此自动关联。",
    alerts: [
      { name: "订单接口 P95 延迟升高", state: "触发中", firstSeen: "09:47:02", duration: "37 分钟", source: "Prometheus", sourceType: "prometheus" },
      { name: "订单创建耗时升高", state: "触发中", firstSeen: "09:49:31", duration: "34 分钟", source: "CloudEvents", sourceType: "cloud" },
    ],
    timeline: [
      { time: "09:47:02", title: "首次告警出现", detail: "订单接口延迟升高", tone: "danger" },
      { time: "09:47:18", title: "创建事故", detail: "系统自动创建事故", tone: "neutral" },
      { time: "09:49:31", title: "关联第二条告警", detail: "订单创建耗时升高", tone: "neutral" },
      { time: "10:24:18", title: "告警仍在持续", detail: "事故持续处理中", tone: "current" },
    ],
  },
  {
    id: "inc-user", title: "用户中心 Pod 反复重启", service: "user-center",
    team: "用户平台组", owner: "未认领", severity: "重要", severityTone: "high",
    state: "处理中", stateTone: "active", environment: "production",
    detectedAt: "2026-08-25 10:12:05", queueTime: "10:12", duration: "12 分钟",
    impact: "用户登录服务出现短时抖动，当前仍有实例重启",
    reason: "2 条告警来自同一生产服务，并在 15 分钟窗口内连续出现，因此自动关联。",
    alerts: [
      { name: "用户中心 Pod 重启", state: "触发中", firstSeen: "10:12:05", duration: "12 分钟", source: "CloudEvents", sourceType: "cloud" },
      { name: "用户中心可用实例不足", state: "触发中", firstSeen: "10:13:22", duration: "11 分钟", source: "Prometheus", sourceType: "prometheus" },
    ],
    timeline: [
      { time: "10:12:05", title: "首次告警出现", detail: "Pod 重启次数超过阈值", tone: "danger" },
      { time: "10:12:17", title: "创建事故", detail: "系统自动创建事故", tone: "neutral" },
      { time: "10:13:22", title: "关联第二条告警", detail: "可用实例不足", tone: "neutral" },
      { time: "10:24:18", title: "告警仍在持续", detail: "事故持续处理中", tone: "current" },
    ],
  },
  {
    id: "inc-search", title: "搜索服务 CPU 使用率过高", service: "search-service",
    team: "搜索平台组", owner: "陈晨", severity: "一般", severityTone: "medium",
    state: "已解决", stateTone: "resolved", environment: "production",
    detectedAt: "2026-08-25 08:44:30", queueTime: "08:44", duration: "48 分钟",
    resolvedAt: "09:32", impact: "搜索请求曾短时变慢，当前服务已恢复",
    reason: "2 条告警来自同一生产服务，并在 15 分钟窗口内连续出现，因此自动关联。",
    alerts: [
      { name: "搜索服务 CPU 使用率过高", state: "已恢复", firstSeen: "08:44:30", duration: "48 分钟", source: "Prometheus", sourceType: "prometheus" },
      { name: "搜索接口延迟升高", state: "已恢复", firstSeen: "08:46:02", duration: "45 分钟", source: "Prometheus", sourceType: "prometheus" },
    ],
    timeline: [
      { time: "08:44:30", title: "首次告警出现", detail: "CPU 使用率超过阈值", tone: "danger" },
      { time: "08:44:43", title: "创建事故", detail: "系统自动创建事故", tone: "neutral" },
      { time: "08:46:02", title: "关联第二条告警", detail: "接口延迟升高", tone: "neutral" },
      { time: "09:32:11", title: "告警已恢复", detail: "事故已由操作员解决", tone: "resolved" },
    ],
  },
];

const navItems = [
  { id: "incidents", label: "事故中心", icon: PhPulse },
  { id: "alerts", label: "告警", icon: PhBell },
  { id: "catalog", label: "服务目录", icon: PhSquaresFour },
  { id: "jobs", label: "关联任务", icon: PhLinkSimple },
  { id: "status", label: "平台状态", icon: PhMonitor },
];

const selectedId = ref("inc-payment");
const environment = ref("production");
const search = ref("");
const claimedIds = ref(new Set());
const technicalOpen = ref(false);
const alertsFocused = ref(false);
const activeNav = ref("incidents");
const menuOpen = ref(false);
const notice = ref("");

const selectedIncident = computed(() => incidents.find((item) => item.id === selectedId.value) ?? incidents[0]);
const visibleIncidents = computed(() => {
  const query = search.value.trim().toLocaleLowerCase("zh-CN");
  return incidents.filter((item) => {
    const envMatches = environment.value === "all" || item.environment === environment.value;
    const content = `${item.title} ${item.service} ${item.team}`.toLocaleLowerCase("zh-CN");
    return envMatches && (!query || content.includes(query));
  });
});
const activeIncidents = computed(() => visibleIncidents.value.filter((item) => item.stateTone !== "resolved"));
const resolvedIncidents = computed(() => visibleIncidents.value.filter((item) => item.stateTone === "resolved"));
const isClaimed = computed(() => claimedIds.value.has(selectedIncident.value.id));

function selectIncident(id) {
  selectedId.value = id;
  technicalOpen.value = false;
  alertsFocused.value = false;
}

function showNotice(message) {
  notice.value = message;
  window.setTimeout(() => (notice.value = ""), 2200);
}

function claimIncident() {
  claimedIds.value = new Set([...claimedIds.value, selectedIncident.value.id]);
  showNotice("事故已由你认领");
}

async function focusAlerts() {
  alertsFocused.value = true;
  await nextTick();
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
      <nav class="nav-list">
        <button v-for="item in navItems" :key="item.id" type="button" :class="['nav-item', { active: activeNav === item.id }]" @click="chooseNav(item)">
          <component :is="item.icon" :size="21" /><span>{{ item.label }}</span>
        </button>
      </nav>
      <div class="sidebar-footer">
        <button type="button" class="nav-item muted" @click="showNotice('系统设置将在后续阶段完善')"><PhGear :size="20" /><span>系统设置</span></button>
        <button type="button" class="nav-item muted" @click="showNotice('导航已收起')"><PhCaretLeft :size="20" /><span>收起导航</span></button>
      </div>
    </aside>

    <main class="main-surface">
      <header class="topbar">
        <div class="title-group">
          <h1>事故中心</h1>
          <div class="environment-control">
            <select v-model="environment" aria-label="环境筛选"><option value="all">全部环境</option><option value="production">生产环境</option><option value="staging">预发环境</option></select>
            <PhCaretDown :size="14" />
          </div>
        </div>
        <label class="search-box"><PhMagnifyingGlass :size="18" /><input v-model="search" aria-label="搜索事故、服务或团队" placeholder="搜索事故、服务或团队" /></label>
        <div class="topbar-meta"><time datetime="2026-08-25T10:24:18+08:00">2026-08-25&nbsp; 10:24:18</time><button type="button" class="avatar-button" aria-label="用户菜单" @click="showNotice('个人菜单将在后续阶段完善')"><span>张</span><PhCaretDown :size="13" /></button></div>
      </header>

      <div class="workspace">
        <section class="incident-queue" aria-label="事故列表">
          <div v-if="visibleIncidents.length === 0" class="empty-state"><PhMagnifyingGlass :size="24" /><strong>没有符合条件的事故</strong><span>请调整环境或搜索条件</span></div>
          <template v-else>
            <section v-if="activeIncidents.length" class="queue-group">
              <div class="queue-heading"><PhCaretDown :size="15" /><span>进行中（{{ activeIncidents.length }}）</span></div>
              <article v-for="incident in activeIncidents" :key="incident.id" :data-testid="`incident-${incident.id}`" :class="['incident-row', { selected: selectedId === incident.id }]">
                <button type="button" @click="selectIncident(incident.id)">
                  <span class="row-title-line"><PhCircle :size="9" weight="fill" :class="`dot-${incident.severityTone}`" /><strong>{{ incident.title }}</strong><time>{{ incident.queueTime }}</time></span>
                  <span class="row-badges"><span :class="['badge', `badge-${incident.severityTone}`]">{{ incident.severity }}</span><span class="badge badge-active">{{ incident.state }}</span></span>
                  <span class="row-service">{{ incident.service }}</span><span class="row-meta">持续 {{ incident.duration }} · 关联 {{ incident.alerts.length }} 条告警</span>
                </button>
              </article>
            </section>
            <section v-if="resolvedIncidents.length" class="queue-group resolved-group">
              <div class="queue-heading"><PhCaretDown :size="15" /><span>已解决（{{ resolvedIncidents.length }}）</span></div>
              <article v-for="incident in resolvedIncidents" :key="incident.id" :data-testid="`incident-${incident.id}`" :class="['incident-row', { selected: selectedId === incident.id }]">
                <button type="button" @click="selectIncident(incident.id)">
                  <span class="row-title-line"><PhCheckCircle :size="14" weight="fill" class="dot-resolved" /><strong>{{ incident.title }}</strong></span>
                  <span class="row-badges"><span class="badge badge-resolved">已解决</span></span><span class="row-service">{{ incident.service }}</span><span class="row-meta">持续 {{ incident.duration }} · 关联 {{ incident.alerts.length }} 条告警</span><span class="row-resolved">已于 {{ incident.resolvedAt }} 解决</span>
                </button>
              </article>
            </section>
            <p class="queue-total">共 {{ visibleIncidents.length }} 条事故</p>
          </template>
        </section>

        <section class="incident-detail" aria-live="polite">
          <header class="detail-header">
            <div class="detail-title-copy"><div class="detail-title-line"><h2 data-testid="incident-title">{{ selectedIncident.title }}</h2><span :class="['badge', `badge-${selectedIncident.severityTone}`]">{{ selectedIncident.severity }}</span><span :class="['badge', `badge-${selectedIncident.stateTone}`]">{{ selectedIncident.state }}</span></div><p>{{ selectedIncident.impact }}</p></div>
            <div class="detail-actions">
              <button type="button" data-testid="claim-incident" :class="['button', 'primary', { claimed: isClaimed }]" :disabled="isClaimed || selectedIncident.stateTone === 'resolved'" @click="claimIncident"><PhUser :size="17" weight="bold" />{{ isClaimed ? "已认领" : selectedIncident.stateTone === "resolved" ? "事故已解决" : "认领事故" }}</button>
              <button type="button" class="button secondary" @click="focusAlerts"><PhBell :size="17" />查看全部告警</button>
              <div class="more-menu-wrap"><button type="button" class="button secondary compact" @click="menuOpen = !menuOpen">更多操作 <PhCaretDown :size="14" /></button><div v-if="menuOpen" class="more-menu"><button type="button" @click="showNotice('事故链接已复制'); menuOpen = false">复制事故链接</button><button type="button" @click="showNotice('操作记录将在后续阶段完善'); menuOpen = false">查看操作记录</button></div></div>
            </div>
          </header>

          <dl class="incident-facts">
            <div><PhBuildings :size="19" /><dt>所属服务</dt><dd>{{ selectedIncident.service }}</dd></div><div><PhUsersThree :size="19" /><dt>所属团队</dt><dd>{{ selectedIncident.team }}</dd></div><div><PhUser :size="19" /><dt>负责人</dt><dd data-testid="incident-owner">{{ isClaimed ? "张工程师" : selectedIncident.owner }}</dd></div><div><PhHeartbeat :size="19" /><dt>发现时间</dt><dd>{{ selectedIncident.detectedAt }}</dd></div>
          </dl>

          <div class="detail-body">
            <div class="detail-main-column">
              <section class="correlation-explanation"><div class="section-title"><PhQuestion :size="20" weight="bold" /><h3>为什么归为同一事故</h3><span class="rule-confirmed"><PhCheckCircle :size="14" weight="fill" />规则确认</span></div><p>{{ selectedIncident.reason }}</p></section>
              <section id="related-alerts" :class="['alerts-section', { focused: alertsFocused }]">
                <div class="content-heading"><h3>关联告警（{{ selectedIncident.alerts.length }}）</h3><span>触发值与阈值：上游未提供</span></div>
                <div class="alerts-table" role="table" aria-label="关联告警"><div class="alerts-head" role="row"><span>告警名称</span><span>状态</span><span>首次出现</span><span>持续时间</span><span>来源</span></div><div v-for="alert in selectedIncident.alerts" :key="alert.name" data-testid="related-alert-row" class="alert-row" role="row"><strong>{{ alert.name }}</strong><span :class="['alert-state', { resolved: alert.state === '已恢复' }]"><PhCircle :size="8" weight="fill" />{{ alert.state }}</span><span>{{ alert.firstSeen }}</span><span>{{ alert.duration }}</span><span class="source-label"><PhCloud v-if="alert.sourceType === 'cloud'" :size="17" /><PhPulse v-else :size="17" />{{ alert.source }}</span></div></div>
              </section>
              <section class="technical-section"><button type="button" data-testid="technical-toggle" :aria-expanded="technicalOpen" @click="technicalOpen = !technicalOpen"><span><PhWrench :size="17" />查看技术详情</span><PhCaretDown :size="15" :class="{ rotated: technicalOpen }" /></button><div v-if="technicalOpen" data-testid="technical-content" class="technical-content"><dl><div><dt>关联规则</dt><dd>correlation.v1</dd></div><div><dt>关联窗口</dt><dd>15 分钟</dd></div><div><dt>环境</dt><dd>production</dd></div><div><dt>候选数量</dt><dd>{{ selectedIncident.alerts.length }}</dd></div></dl><p>技术信息仅用于排查关联逻辑，不代表根因分析结论。</p></div></section>
            </div>
            <aside class="timeline" aria-label="事件时间线"><h3>事件时间线</h3><ol><li v-for="event in selectedIncident.timeline" :key="`${event.time}-${event.title}`" :class="`tone-${event.tone}`"><span class="timeline-marker"><PhCircle :size="13" weight="fill" /></span><time>{{ event.time }}</time><strong>{{ event.title }}</strong><p>{{ event.detail }}</p></li></ol></aside>
          </div>
        </section>
      </div>
    </main>
    <Transition name="toast"><div v-if="notice" class="toast" role="status"><PhCheckCircle :size="18" weight="fill" />{{ notice }}</div></Transition>
  </div>
</template>
