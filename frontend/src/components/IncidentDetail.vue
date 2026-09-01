<script setup>
import { ref } from "vue";
import { PhBellRinging, PhCheckCircle, PhClockCounterClockwise, PhX } from "@phosphor-icons/vue";

defineProps({
  detail: { type: Object, required: true },
  operationState: { type: String, default: "idle" },
  operationError: { type: String, default: "" },
  resolutionSummary: { type: String, default: "" },
});
defineEmits(["close", "acknowledge", "resolve", "update:resolutionSummary"]);
const resolving = ref(false);
</script>

<template>
  <section data-testid="incident-detail" class="incident-ops-detail">
    <header class="incident-detail-header">
      <div><span>{{ detail.incident.reference }}</span><h2>{{ detail.incident.title }}</h2></div>
      <div class="incident-detail-actions">
        <button v-if="detail.incident.state === 'OPEN'" data-testid="acknowledge-incident" type="button" class="button secondary" :disabled="operationState === 'pending'" @click="$emit('acknowledge')"><PhCheckCircle :size="16" />确认</button>
        <button v-if="detail.incident.state !== 'RESOLVED'" data-testid="open-resolve" type="button" class="button primary" :disabled="operationState === 'pending'" @click="resolving = true">解决</button>
        <button type="button" class="icon-close" aria-label="关闭详情" @click="$emit('close')"><PhX :size="18" /></button>
      </div>
    </header>

    <div v-if="operationError" :class="['incident-operation-message', operationState]">{{ operationError }}</div>
    <form v-if="resolving" class="incident-resolve-panel" @submit.prevent="$emit('resolve')">
      <label>解决说明<textarea data-testid="resolution-summary" :value="resolutionSummary" maxlength="2000" placeholder="说明恢复结果和已完成的处置" @input="$emit('update:resolutionSummary', $event.target.value)" /></label>
      <div><span>{{ resolutionSummary.length }} / 2000</span><button type="button" class="button secondary" @click="resolving = false">取消</button><button type="submit" class="button primary" :disabled="operationState === 'pending'">确认解决</button></div>
    </form>

    <div class="incident-detail-columns">
      <section class="incident-detail-column current">
        <header><PhBellRinging :size="18" /><h3>当前情况</h3></header>
        <dl class="incident-current-facts">
          <div><dt>状态</dt><dd>{{ detail.incident.stateLabel }}</dd></div>
          <div><dt>严重级别</dt><dd>{{ detail.incident.severityLabel }}</dd></div>
          <div><dt>环境</dt><dd>{{ detail.incident.environmentLabel }}</dd></div>
          <div><dt>影响对象</dt><dd>{{ detail.incident.group_display_name }}</dd></div>
          <div><dt>告警</dt><dd>{{ detail.incident.alertSummary }}</dd></div>
          <div><dt>创建规则</dt><dd>{{ detail.incident.rule_name }}</dd></div>
        </dl>
        <article class="incident-rule-explanation"><span>生成依据</span><p>{{ detail.incident.rule_summary }}</p></article>
        <article v-if="detail.incident.resolution_summary" class="incident-rule-explanation"><span>解决说明</span><p>{{ detail.incident.resolution_summary }}</p></article>
      </section>

      <section class="incident-detail-column alerts">
        <header><h3>关联告警</h3><span>{{ detail.alerts.length }} 条</span></header>
        <p v-if="!detail.alerts.length" class="incident-column-empty">当前没有可展示的关联告警</p>
        <details v-for="alert in detail.alerts" :key="alert.id" class="incident-alert-record">
          <summary><span :class="['severity-dot', alert.severity]"></span><strong>{{ alert.name }}</strong><small>{{ alert.stateLabel }}</small></summary>
          <dl><div><dt>摘要</dt><dd>{{ alert.summary || '—' }}</dd></div><div><dt>对象</dt><dd>{{ alert.entityName }}</dd></div><div><dt>来源</dt><dd>{{ alert.sourceName }}</dd></div><div><dt>最近接收</dt><dd>{{ alert.lastReceivedAtLabel }}</dd></div></dl>
          <p v-if="alert.description">{{ alert.description }}</p>
        </details>
        <small v-if="detail.alerts_truncated" class="incident-truncated">关联告警较多，当前只展示前 500 条</small>
      </section>

      <section class="incident-detail-column activity">
        <header><PhClockCounterClockwise :size="18" /><h3>处置与飞书</h3></header>
        <div class="feishu-status-line"><span>飞书协同</span><strong>{{ detail.feishu.statusLabel }}</strong><small v-if="detail.feishu.route_name">{{ detail.feishu.route_name }}</small></div>
        <p v-if="!detail.activities.length" class="incident-column-empty">还没有处置记录</p>
        <ol v-else class="incident-activity-list">
          <li v-for="activity in detail.activities" :key="activity.id"><i></i><div><span>{{ activity.kindLabel }} · {{ activity.actorLabel }}</span><p>{{ activity.summary }}</p><time>{{ activity.occurredAtLabel }}</time></div></li>
        </ol>
        <small v-if="detail.activities_truncated" class="incident-truncated">处置记录较多，当前只展示最近 1,000 条</small>
      </section>
    </div>
  </section>
</template>
