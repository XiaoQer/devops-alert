<script setup>
import { computed, reactive, ref } from "vue";
import { PhArrowRight, PhBell, PhCheckCircle, PhCircle, PhQuestion, PhSiren, PhStack } from "@phosphor-icons/vue";

import { useAlertGroupCenter } from "../composables/useAlertGroupCenter";
import AlertCenter from "./AlertCenter.vue";

defineEmits(["open-incident"]);
const mode = ref("events");
const {
  view, state, severity, environment, storm, linked, search, groups, summary, selectedId, detail, members,
  pendingMembers, pendingTotal, pendingState, operationState, operationError, confirmPending,
  splitMembers, mergeGroup,
  total, memberTotal, listState, summaryState, detailState, memberState, listError, summaryError,
  detailError, memberError, hasPrevious, hasNext, rangeStart, rangeEnd, memberHasPrevious,
  memberHasNext, memberRangeStart, memberRangeEnd, loadList, loadSummary, loadDetail, loadMembers,
  goPrevious, goNext, goMemberPrevious, goMemberNext,
} = useAlertGroupCenter();
const pendingReasons = reactive({});
const selectedMemberIds = ref([]);
const splitReason = ref("");
const mergeSourceId = ref("");
const mergeReason = ref("");
const mergeCandidates = computed(() => groups.value.filter((item) => item.id !== detail.value?.id && item.environment === detail.value?.environment));

function confirmMember(alertId) {
  return confirmPending(alertId, pendingReasons[alertId] || "人工确认属于当前事件");
}

async function splitSelected() {
  if (await splitMembers(selectedMemberIds.value, splitReason.value)) {
    selectedMemberIds.value = []; splitReason.value = "";
  }
}

async function mergeSelected() {
  if (await mergeGroup(mergeSourceId.value, mergeReason.value)) {
    mergeSourceId.value = ""; mergeReason.value = "";
  }
}

function selectEventView(next) {
  mode.value = "events";
  view.value = next;
}
</script>

<template>
  <div class="alert-group-shell">
    <header class="alert-view-switcher">
      <div><strong>告警中心</strong><span>把大量原始告警收敛成少量可理解的事件</span></div>
      <div role="tablist" aria-label="告警视图">
        <button type="button" :class="{ active: mode === 'events' && view === 'current' }" role="tab" :aria-selected="mode === 'events' && view === 'current'" @click="selectEventView('current')">当前事件</button>
        <button type="button" :class="{ active: mode === 'events' && view === 'pending' }" role="tab" :aria-selected="mode === 'events' && view === 'pending'" @click="selectEventView('pending')">待确认</button>
        <button type="button" :class="{ active: mode === 'events' && view === 'history' }" role="tab" :aria-selected="mode === 'events' && view === 'history'" @click="selectEventView('history')">历史事件</button>
        <button type="button" :class="{ active: mode === 'raw' }" role="tab" :aria-selected="mode === 'raw'" @click="mode = 'raw'">原始告警</button>
      </div>
    </header>

    <AlertCenter v-if="mode === 'raw'" @open-incident="$emit('open-incident', $event)" />
    <div v-else data-testid="alert-group-center" class="alert-center-page alert-group-page">
      <section class="alert-summary-strip group-summary-strip" aria-label="24 小时告警归集概况">
        <div v-if="summaryState === 'error'" class="summary-error"><span>{{ summaryError }}</span><button class="link-button" type="button" @click="loadSummary">重试</button></div>
        <template v-else>
          <article><span>当前活跃事件</span><strong>{{ summary?.activeGroups ?? "—" }}</strong></article>
          <article><span>当前严重事件</span><strong>{{ summary?.severeActiveGroups ?? "—" }}</strong></article>
          <article><span>当前活动告警</span><strong>{{ summary?.activeAlerts ?? "—" }}</strong></article>
          <article><span>{{ summary?.windowText ?? "近 24 小时" }}已恢复</span><strong>{{ summary?.resolvedGroups ?? "—" }}</strong></article>
          <article><span>{{ summary?.windowText ?? "近 24 小时" }}降噪率</span><strong>{{ summary?.compressionText ?? "—" }}</strong></article>
          <article><span>当前告警风暴</span><strong>{{ summary?.stormGroups ?? "—" }}</strong></article>
        </template>
      </section>

      <section class="alert-filter-bar group-filter-bar">
        <input v-model="search" aria-label="搜索告警组或服务" placeholder="搜索问题、服务或异常类型" />
        <select v-model="state" aria-label="告警组状态"><option value="">全部状态</option><option value="ACTIVE">告警中</option><option value="RESOLVED">已恢复</option></select>
        <select v-model="severity" aria-label="告警组级别"><option value="">全部级别</option><option value="critical">严重</option><option value="high">重要</option><option value="medium">一般</option><option value="low">提示</option></select>
        <select v-model="environment" aria-label="告警组环境"><option value="">全部环境</option><option value="production">生产环境</option><option value="staging">预发环境</option><option value="development">开发环境</option></select>
        <select v-model="storm" aria-label="告警风暴"><option value="">全部流量</option><option value="STORM">仅告警风暴</option><option value="NORMAL">正常流量</option></select>
        <select v-model="linked" aria-label="事故关联"><option value="">全部判定状态</option><option value="true">已形成事故</option><option value="false">尚未形成事故</option></select>
      </section>

      <div class="alert-workspace group-workspace">
        <section class="alert-list-panel group-list-panel" aria-label="告警组列表">
          <div v-if="listState === 'loading'" class="empty-state"><PhStack :size="24" /><strong>正在归集告警</strong></div>
          <div v-else-if="listState === 'error'" class="empty-state error-state"><PhQuestion :size="24" /><strong>{{ listError }}</strong><button type="button" class="button secondary" @click="loadList">重新加载</button></div>
          <div v-else-if="listState === 'empty'" class="empty-state"><PhCheckCircle :size="24" /><strong>当前没有符合条件的告警事件</strong><span>这里不会使用演示数据填充</span></div>
          <template v-else>
            <button v-for="group in groups" :key="group.id" type="button" :class="['alert-list-item', 'group-list-item', { selected: selectedId === group.id }]" @click="loadDetail(group.id)">
              <span class="alert-item-title"><PhCircle :size="8" weight="fill" :class="`dot-${group.severityTone}`" /><strong>{{ group.title }}</strong></span>
              <span class="alert-item-badges"><span :class="['badge', `badge-${group.severityTone}`]">{{ group.severity }}</span><span :class="['badge', `badge-${group.stateTone}`]">{{ group.state }}</span><span v-if="group.storm" class="badge badge-storm">{{ group.stormText }}</span></span>
              <span>{{ group.scopeText }} · {{ group.environment }} · {{ group.symptom }}</span>
              <small>{{ group.memberText }} · {{ group.resourceText }}</small><small>{{ group.duration }} · {{ group.incidentText }}</small>
            </button>
            <footer class="alert-pagination"><span>第 {{ rangeStart }}–{{ rangeEnd }} 组，共 {{ total }} 组</span><div><button type="button" class="link-button" :disabled="!hasPrevious" @click="goPrevious">上一页</button><button type="button" class="link-button" :disabled="!hasNext" @click="goNext">下一页</button></div></footer>
          </template>
        </section>

        <section class="alert-detail-panel" aria-live="polite">
          <div v-if="detailState === 'loading'" class="detail-placeholder"><PhStack :size="28" /><strong>正在读取告警组详情</strong></div>
          <div v-else-if="detailState === 'error'" class="detail-placeholder error-state"><PhQuestion :size="28" /><strong>{{ detailError }}</strong><button type="button" class="button secondary" @click="loadDetail(selectedId)">重新加载详情</button></div>
          <div v-else-if="!detail" class="detail-placeholder"><PhStack :size="28" /><strong>选择事件后查看归集结果</strong></div>
          <template v-else>
            <header class="alert-detail-header group-detail-header"><div><div class="detail-title-line"><h2>{{ detail.title }}</h2><span :class="['badge', `badge-${detail.severityTone}`]">{{ detail.severity }}</span><span v-if="detail.storm" class="badge badge-storm"><PhSiren :size="13" />{{ detail.stormText }}</span></div><p>{{ detail.scopeText }} · {{ detail.service }} · {{ detail.environment }} · {{ detail.symptom }}</p></div><button v-if="detail.incident" data-testid="open-group-incident" type="button" class="button primary" @click="$emit('open-incident', detail.incident.id)">进入关联事故<PhArrowRight :size="16" /></button></header>
            <div class="group-detail-body">
              <section class="group-explanation"><header><h3>发生了什么</h3><span>{{ detail.memberText }}</span></header><p>{{ detail.reason }}</p><div class="group-impact-facts"><span><strong>{{ detail.activeCount }}</strong> 条仍在告警</span><span><strong>{{ detail.resourceCount }}</strong> 个影响资源</span><span><strong>{{ detail.sources.length }}</strong> 个告警来源</span><span><strong>{{ detail.duration.replace('持续 ', '') }}</strong> 已持续</span></div></section>
              <section class="group-explanation incident-decision-card"><header><h3>是否需要事故处置</h3><strong>{{ detail.incidentDecision.label }}</strong></header><p>{{ detail.incidentDecision.explanation }}</p></section>
              <section class="group-explanation"><header><h3>为什么归到一起</h3><span v-if="detail.grouping.dimensions.length">匹配度 {{ detail.grouping.totalScore }} 分</span></header><p>{{ detail.grouping.explanation }}</p><div v-if="detail.grouping.dimensions.length" class="group-impact-facts"><span v-for="item in detail.grouping.dimensions" :key="item.label"><strong>{{ item.score }}/{{ item.maximum }}</strong> {{ item.label }}</span></div></section>
              <section v-if="detail.timeline.length" class="group-resources"><h3>事件传播时间线</h3><ul><li v-for="item in detail.timeline" :key="`${item.alert_id}-${item.occurred_at}-${item.kind}`"><span>{{ item.occurredAt }}</span><strong>{{ item.label }}</strong><small>{{ item.explanation }}</small></li></ul></section>
              <section class="group-members pending-members"><div class="content-heading"><div><h3>待确认成员</h3><span>系统没有足够把握时由你决定是否归入</span></div><strong>{{ pendingTotal }} 条</strong></div>
                <div v-if="pendingState === 'loading'" class="compact-empty">正在读取待确认成员</div>
                <div v-else-if="pendingState === 'error'" class="compact-empty error-state">待确认成员暂时不可用</div>
                <div v-else-if="pendingState === 'empty'" class="compact-empty">当前没有需要人工确认的告警</div>
                <div v-else class="pending-member-list">
                  <article v-for="item in pendingMembers" :key="item.id" class="pending-member-card">
                    <div><strong>{{ item.title }}</strong><span>{{ item.source_name }} · 匹配度 {{ item.total_score }} 分</span><p>{{ item.reason }}</p></div>
                    <input v-model="pendingReasons[item.id]" :aria-label="`确认理由：${item.title}`" maxlength="500" placeholder="补充确认理由（可选）" />
                    <button class="button primary" type="button" :disabled="operationState === 'pending'" @click="confirmMember(item.id)">确认归入</button>
                  </article>
                </div>
                <p v-if="operationError" class="operation-error-banner" role="alert">{{ operationError }}</p>
              </section>
              <section class="group-distributions"><article><h3>告警来源</h3><ul><li v-for="source in detail.sources" :key="source.name"><span>{{ source.name }}</span><strong>{{ source.count }} 条</strong></li></ul></article><article><h3>严重程度</h3><ul><li v-for="item in detail.severities" :key="item.name"><span>{{ item.name }}</span><strong>{{ item.count }} 条</strong></li></ul></article></section>
              <section class="group-resources"><h3>主要影响资源</h3><p v-if="!detail.resources.length">暂未识别到具体资源</p><ul v-else><li v-for="resource in detail.resources" :key="`${resource.type}-${resource.name}`"><span>{{ resource.type }}</span><strong>{{ resource.name }}</strong><small>{{ resource.count }} 条告警</small></li></ul></section>
              <section class="group-members"><div class="content-heading"><div><h3>原始告警明细</h3><span>每条告警仍然保留，可独立审计</span></div><strong>{{ memberTotal }} 条</strong></div>
                <div v-if="memberState === 'loading'" class="compact-empty">正在读取原始告警</div>
                <div v-else-if="memberState === 'error'" class="compact-empty error-state">{{ memberError }} <button type="button" class="link-button" @click="loadMembers">重试</button></div>
                <div v-else-if="memberState === 'empty'" class="compact-empty">当前组内没有原始告警</div>
                <div v-else class="group-member-table"><div class="group-member-head"><span>选择 / 告警名称</span><span>状态</span><span>级别</span><span>来源</span><span>最后出现</span></div><div v-for="member in members" :key="member.id" class="group-member-row"><strong><input v-model="selectedMemberIds" type="checkbox" :value="member.id" :aria-label="`选择拆分：${member.title}`" />{{ member.title }}</strong><span :class="['badge', `badge-${member.stateTone}`]">{{ member.state }}</span><span>{{ member.severity }}</span><span>{{ member.sourceName }}</span><time>{{ member.lastObservedAt }}</time></div></div>
                <div v-if="selectedMemberIds.length" class="event-operation-bar"><input v-model="splitReason" aria-label="拆分原因" maxlength="500" placeholder="说明为什么这些告警属于独立事件" /><button class="button secondary" type="button" :disabled="operationState === 'pending' || !splitReason.trim()" @click="splitSelected">拆分为新事件</button></div>
                <footer v-if="memberState === 'ready'" class="alert-pagination member-pagination"><span>第 {{ memberRangeStart }}–{{ memberRangeEnd }} 条，共 {{ memberTotal }} 条</span><div><button type="button" class="link-button" :disabled="!memberHasPrevious" @click="goMemberPrevious">上一页</button><button type="button" data-testid="member-next-page" class="link-button" :disabled="!memberHasNext" @click="goMemberNext">下一页</button></div></footer>
              </section>
              <section v-if="mergeCandidates.length" class="group-members"><div class="content-heading"><div><h3>合并事件</h3><span>只显示当前列表中同环境的兼容候选</span></div></div><div class="event-operation-bar"><select v-model="mergeSourceId" aria-label="选择待合并事件"><option value="">选择另一个事件</option><option v-for="item in mergeCandidates" :key="item.id" :value="item.id">{{ item.title }} · {{ item.service }}</option></select><input v-model="mergeReason" aria-label="合并原因" maxlength="500" placeholder="说明为什么属于同一次故障" /><button class="button secondary" type="button" :disabled="operationState === 'pending' || !mergeSourceId || !mergeReason.trim()" @click="mergeSelected">合并事件</button></div></section>
            </div>
          </template>
        </section>
      </div>
    </div>
  </div>
</template>
