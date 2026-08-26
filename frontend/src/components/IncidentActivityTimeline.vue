<script setup>
defineProps({
  activities: { type: Array, default: () => [] },
  truncated: { type: Boolean, default: false },
});
</script>

<template>
  <section class="activity-panel" data-testid="activity-timeline">
    <div class="content-heading">
      <h3>处置时间线</h3>
      <span v-if="truncated">仅展示前 200 条</span>
      <span v-else>{{ activities.length }} 条记录</span>
    </div>
    <div v-if="!activities.length" class="activity-empty">尚无人工处置记录</div>
    <ol v-else class="activity-list">
      <li v-for="activity in activities" :key="activity.id" :class="`tone-${activity.tone}`">
        <span class="activity-dot" />
        <div class="activity-copy">
          <div class="activity-title">
            <strong>{{ activity.title }}</strong>
            <span v-if="activity.category" class="activity-category">{{ activity.category }}</span>
            <span v-if="activity.resolutionCategory" class="activity-category resolved">{{ activity.resolutionCategory }}</span>
            <time>{{ activity.time }}</time>
          </div>
          <p>{{ activity.message }}</p>
          <dl v-if="activity.actions || activity.resolutionCategory" class="activity-resolution">
            <div v-if="activity.actions"><dt>采取措施</dt><dd>{{ activity.actions }}</dd></div>
            <div><dt>根因</dt><dd>{{ activity.rootCause }}</dd></div>
          </dl>
          <small>{{ activity.actor }} · 事故版本 {{ activity.version }}</small>
        </div>
      </li>
    </ol>
  </section>
</template>
