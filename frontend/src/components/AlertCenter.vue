<script setup>
import { PhArrowRight, PhBell, PhCheckCircle, PhCircle, PhQuestion, PhWarning } from "@phosphor-icons/vue";

import { useAlertCenter } from "../composables/useAlertCenter";

defineEmits(["open-incident"]);
const { state, severity, environment, linked, search, alerts, summary, selectedId, detail, listState, summaryState, detailState, listError, summaryError, detailError, loadList, loadSummary, loadDetail, selectAlert } = useAlertCenter();
</script>

<template>
  <div data-testid="alert-center" class="alert-center-page">
    <section class="alert-summary-strip" aria-label="24 小时告警概况">
      <div v-if="summaryState === 'error'" class="summary-error"><span>{{ summaryError }}</span><button class="link-button" type="button" @click="loadSummary">重试</button></div>
      <template v-else><article><span>当前告警</span><strong>{{ summary?.active ?? "—" }}</strong></article><article><span>严重与重要</span><strong>{{ summary?.severeActive ?? "—" }}</strong></article><article><span>24 小时已恢复</span><strong>{{ summary?.resolved ?? "—" }}</strong></article><article><span>尚未关联事故</span><strong>{{ summary?.unlinkedActive ?? "—" }}</strong></article></template>
    </section>
    <section class="alert-filter-bar">
      <input v-model="search" aria-label="搜索告警或服务" placeholder="搜索告警或服务" />
      <select v-model="state" aria-label="告警状态"><option value="">全部状态</option><option value="ACTIVE">告警中</option><option value="RESOLVED">已恢复</option><option value="SUPPRESSED">已抑制</option></select>
      <select v-model="severity" aria-label="告警级别"><option value="">全部级别</option><option value="critical">严重</option><option value="high">重要</option><option value="medium">一般</option><option value="low">提示</option></select>
      <select v-model="environment" aria-label="告警环境"><option value="">全部环境</option><option value="production">生产环境</option><option value="staging">预发环境</option><option value="development">开发环境</option></select>
      <select v-model="linked" aria-label="事故关联"><option value="">全部关联状态</option><option value="true">已关联事故</option><option value="false">未关联事故</option></select>
    </section>
    <div class="alert-workspace">
      <section class="alert-list-panel" aria-label="告警列表">
        <div v-if="listState === 'loading'" class="empty-state"><PhBell :size="24" /><strong>正在读取告警</strong></div>
        <div v-else-if="listState === 'error'" class="empty-state error-state"><PhQuestion :size="24" /><strong>{{ listError }}</strong><button type="button" class="button secondary" @click="loadList">重新加载</button></div>
        <div v-else-if="listState === 'empty'" class="empty-state"><PhCheckCircle :size="24" /><strong>当前没有符合条件的告警</strong><span>调整筛选条件后可重新查看</span></div>
        <template v-else><button v-for="alert in alerts" :key="alert.id" type="button" :class="['alert-list-item', { selected: selectedId === alert.id }]" @click="selectAlert(alert.id)"><span class="alert-item-title"><PhCircle :size="8" weight="fill" :class="`dot-${alert.severityTone}`" /><strong>{{ alert.title }}</strong></span><span class="alert-item-badges"><span :class="['badge', `badge-${alert.severityTone}`]">{{ alert.severity }}</span><span :class="['badge', `badge-${alert.stateTone}`]">{{ alert.state }}</span></span><span>{{ alert.service }} · {{ alert.environment }}</span><small>{{ alert.sourceName }} · {{ alert.signalCount }} 条信号</small></button></template>
      </section>
      <section class="alert-detail-panel" aria-live="polite">
        <div v-if="detailState === 'loading'" class="detail-placeholder"><PhBell :size="28" /><strong>正在读取告警详情</strong></div>
        <div v-else-if="detailState === 'error'" class="detail-placeholder error-state"><PhQuestion :size="28" /><strong>{{ detailError }}</strong><button type="button" class="button secondary" @click="loadDetail(selectedId)">重新加载详情</button></div>
        <div v-else-if="!detail" class="detail-placeholder"><PhBell :size="28" /><strong>选择告警后查看检测结果</strong></div>
        <template v-else>
          <header class="alert-detail-header"><div><div class="detail-title-line"><h2>{{ detail.title }}</h2><span :class="['badge', `badge-${detail.severityTone}`]">{{ detail.severity }}</span><span :class="['badge', `badge-${detail.stateTone}`]">{{ detail.state }}</span></div><p>{{ detail.service }} · {{ detail.environment }} · {{ detail.sourceName }}</p></div><button v-if="detail.incident" data-testid="open-linked-incident" type="button" class="button primary" @click="$emit('open-incident', detail.incident.id)">进入关联事故<PhArrowRight :size="16" /></button></header>
          <div class="alert-detail-grid">
            <section class="evidence-card"><header><PhWarning :size="19" /><h3>实际检测结果</h3></header><strong>{{ detail.resultText }}</strong><dl><div><dt>检测时间</dt><dd>{{ detail.detectedAt }}</dd></div><div><dt>平台收到</dt><dd>{{ detail.receivedAt }}</dd></div></dl><ul v-if="detail.facts.length"><li v-for="fact in detail.facts" :key="fact.name"><span>{{ fact.name }}</span><strong>{{ fact.value }}</strong></li></ul></section>
            <section class="correlation-card"><h3>事故关联</h3><p>{{ detail.correlationText }}</p><div v-if="detail.incident" class="linked-incident"><span>已进入事故</span><strong>{{ detail.incident.title }}</strong></div><div v-else class="linked-incident waiting"><span>当前状态</span><strong>尚未关联事故</strong></div></section>
            <section class="processing-card"><h3>系统处理过程</h3><ol><li v-for="(step, index) in detail.steps" :key="`${index}-${step.title}`" :class="`step-${step.status}`"><span>{{ index + 1 }}</span><div><strong>{{ step.title }}</strong><p>{{ step.detail }}</p></div></li></ol></section>
          </div>
        </template>
      </section>
    </div>
  </div>
</template>
