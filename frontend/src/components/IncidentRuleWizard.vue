<script setup>
import { computed, ref } from "vue";
import {
  PhArrowLeft, PhCheck, PhCopy, PhFlask, PhPause, PhPlus, PhTrash, PhWarning,
} from "@phosphor-icons/vue";

const props = defineProps({
  draft: { type: Object, required: true },
  currentRule: { type: Object, default: null },
  step: { type: Number, required: true },
  dryRunResult: { type: Object, default: null },
  operationState: { type: String, required: true },
  operationError: { type: String, default: "" },
  sources: { type: Array, default: () => [] },
});
const emit = defineEmits([
  "close", "step-change", "draft-change", "config-change", "save", "save-and-continue",
  "dry-run", "publish", "disable", "remove", "copy", "reload",
]);

const historyHours = ref(6);
const steps = ["基本信息", "匹配范围", "触发条件", "试运行与发布"];
const countTypes = new Set(["DISTINCT_ALERT_NAMES_GTE", "ACTIVE_ALERTS_GTE", "DISTINCT_ENTITIES_GTE"]);
const conditionOptions = [
  ["DISTINCT_ALERT_NAMES_GTE", "不同 Alertname 数量"],
  ["ACTIVE_ALERTS_GTE", "活动告警数量"],
  ["DISTINCT_ENTITIES_GTE", "不同实体数量"],
  ["MAX_SEVERITY_AT_LEAST", "最高告警级别至少为"],
];
const severityLabels = { critical: "严重", high: "高", medium: "中", low: "低" };
const isDraft = computed(() => !props.currentRule || props.currentRule.state === "DRAFT");
const isBusy = computed(() => props.operationState === "pending");
const hasCountCondition = computed(() => props.draft.config.conditions.some((item) => countTypes.has(item.type)));
const complete = computed(() => Boolean(
  props.draft.name.trim()
  && props.draft.config.environment.trim()
  && props.draft.config.window_minutes >= 1
  && props.draft.config.window_minutes <= 60
  && props.draft.config.conditions.length
  && hasCountCondition.value,
));
const sourceIds = computed(() => new Set(props.draft.config.alert_source_ids));
const conditionPreview = computed(() => props.draft.config.conditions.map(conditionText));

function field(name, event) { emit("draft-change", { [name]: event.target.value }); }
function config(name, value) { emit("config-change", { [name]: value }); }
function toggleSource(sourceId) {
  const ids = new Set(sourceIds.value);
  ids.has(sourceId) ? ids.delete(sourceId) : ids.add(sourceId);
  config("alert_source_ids", [...ids]);
}
function updateServices(event) {
  config("services", event.target.value.split(",").map((item) => item.trim()).filter(Boolean).slice(0, 50));
}
function addCondition() {
  const used = new Set(props.draft.config.conditions.map((item) => item.type));
  const nextType = conditionOptions.find(([type]) => !used.has(type))?.[0];
  if (!nextType) return;
  const next = nextType === "MAX_SEVERITY_AT_LEAST"
    ? { type: nextType, severity: "high" }
    : { type: nextType, threshold: 2 };
  config("conditions", [...props.draft.config.conditions, next]);
}
function updateCondition(index, patch) {
  config("conditions", props.draft.config.conditions.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
}
function changeConditionType(index, type) {
  const next = type === "MAX_SEVERITY_AT_LEAST"
    ? { type, severity: "high" }
    : { type, threshold: 2 };
  config("conditions", props.draft.config.conditions.map((item, itemIndex) => itemIndex === index ? next : item));
}
function conditionTypeUsedByAnother(type, index) {
  return props.draft.config.conditions.some((item, itemIndex) => itemIndex !== index && item.type === type);
}
function removeCondition(index) {
  config("conditions", props.draft.config.conditions.filter((_, itemIndex) => itemIndex !== index));
}
function conditionText(condition) {
  if (condition.type === "DISTINCT_ALERT_NAMES_GTE") return `不同 Alertname 数量不少于 ${condition.threshold}`;
  if (condition.type === "ACTIVE_ALERTS_GTE") return `活动告警数量不少于 ${condition.threshold}`;
  if (condition.type === "DISTINCT_ENTITIES_GTE") return `不同实体数量不少于 ${condition.threshold}`;
  return `最高告警级别至少为${severityLabels[condition.severity] ?? condition.severity}`;
}
function next() {
  if (props.step < 3) emit("step-change", props.step + 1);
  else emit("save-and-continue");
}
</script>

<template>
  <section data-testid="incident-rule-wizard" class="rule-wizard-page">
    <header class="rule-wizard-header">
      <button type="button" class="rule-back" @click="emit('close')"><PhArrowLeft :size="17" />返回规则列表</button>
      <div><h2>{{ currentRule ? currentRule.name : "创建 Incident 规则" }}</h2><p>{{ currentRule ? `规则版本 ${currentRule.version}` : "新规则将先保存为草稿" }}</p></div>
      <span :class="['rule-state-pill', currentRule?.state?.toLowerCase() ?? 'draft']">{{ currentRule?.state === "PUBLISHED" ? "已发布" : currentRule?.state === "DISABLED" ? "已停用" : "草稿" }}</span>
    </header>

    <div class="rule-wizard-layout">
      <nav class="rule-steps" aria-label="创建步骤">
        <button v-for="(label, index) in steps" :key="label" type="button" :class="['rule-step', { active: step === index + 1, done: step > index + 1 }]" :disabled="!isDraft && step !== index + 1" @click="emit('step-change', index + 1)">
          <span><PhCheck v-if="step > index + 1" :size="13" weight="bold" /><template v-else>{{ index + 1 }}</template></span>
          <div><strong>{{ label }}</strong><small>{{ index === 0 ? "名称与用途" : index === 1 ? "环境与聚合边界" : index === 2 ? "窗口与判断条件" : "用真实告警验证" }}</small></div>
        </button>
      </nav>

      <main class="rule-form-panel">
        <div v-if="operationError" class="rule-operation-error"><PhWarning :size="17" />{{ operationError }}<button v-if="operationState === 'conflict'" type="button" @click="emit('reload')">重新加载</button></div>

        <section v-if="step === 1" class="rule-form-section">
          <header><span>步骤 1 / 4</span><h3>基本信息</h3><p>给规则一个容易被值班人员理解的名称和用途说明。</p></header>
          <label class="rule-field"><span>规则名称 <b>*</b></span><input :value="draft.name" :disabled="!isDraft" maxlength="128" placeholder="例如：支付服务多类告警" @input="field('name', $event)" /></label>
          <label class="rule-field"><span>用途说明</span><textarea :value="draft.description" :disabled="!isDraft" maxlength="1000" rows="5" placeholder="说明这条规则希望识别什么情况" @input="field('description', $event)" /></label>
        </section>

        <section v-else-if="step === 2" class="rule-form-section">
          <header><span>步骤 2 / 4</span><h3>匹配范围</h3><p>规则永远不会跨环境合并告警；来源和服务可以留空。</p></header>
          <label class="rule-field"><span>环境 <b>*</b></span><input :value="draft.config.environment" :disabled="!isDraft" list="rule-environments" placeholder="例如 production" @input="config('environment', $event.target.value)" /><datalist id="rule-environments"><option v-for="source in sources" :key="source.environment" :value="source.environment">{{ source.environment_name }}</option></datalist></label>
          <fieldset class="rule-field rule-source-options"><legend>接入源（可选）</legend><p v-if="!sources.length">不限制接入源</p><label v-for="source in sources" :key="source.id"><input type="checkbox" :checked="sourceIds.has(source.id)" :disabled="!isDraft" @change="toggleSource(source.id)" /><span>{{ source.name }}</span><small>{{ source.environment_name || source.environment }}</small></label></fieldset>
          <label class="rule-field"><span>服务（可选，使用英文逗号分隔）</span><input :value="draft.config.services.join(', ')" :disabled="!isDraft" placeholder="checkout, payment" @change="updateServices" /></label>
          <fieldset class="rule-field rule-group-options"><legend>聚合方式 <b>*</b></legend><label><input type="radio" value="SERVICE" :checked="draft.config.group_by === 'SERVICE'" :disabled="!isDraft" @change="config('group_by', 'SERVICE')" /><span>同一服务</span><small>缺少 service 的告警不参与</small></label><label><input type="radio" value="ENTITY" :checked="draft.config.group_by === 'ENTITY'" :disabled="!isDraft" @change="config('group_by', 'ENTITY')" /><span>同一实体</span><small>使用告警已有实体身份</small></label></fieldset>
        </section>

        <section v-else-if="step === 3" class="rule-form-section">
          <header><span>步骤 3 / 4</span><h3>触发条件</h3><p>同一窗口内的全部条件都满足时，试运行才会记为一次命中。</p></header>
          <label class="rule-field compact"><span>观察窗口</span><div class="rule-number-input"><input type="number" min="1" max="60" :value="draft.config.window_minutes" :disabled="!isDraft" @input="config('window_minutes', Number($event.target.value))" /><span>分钟</span></div></label>
          <div class="rule-condition-list">
            <article v-for="(condition, index) in draft.config.conditions" :key="condition.type" class="rule-condition-row">
              <span class="condition-order">{{ index + 1 }}</span>
              <div>
                <select data-testid="condition-type" :aria-label="`条件 ${index + 1} 类型`" :value="condition.type" :disabled="!isDraft" @change="changeConditionType(index, $event.target.value)">
                  <option v-for="[type, label] in conditionOptions" :key="type" :value="type" :disabled="conditionTypeUsedByAnother(type, index)">{{ label }}</option>
                </select>
                <small>{{ conditionText(condition) }}</small>
              </div>
              <select v-if="condition.type === 'MAX_SEVERITY_AT_LEAST'" :value="condition.severity" :disabled="!isDraft" @change="updateCondition(index, { severity: $event.target.value })"><option value="critical">严重</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select>
              <input v-else type="number" min="1" max="1000" :value="condition.threshold" :disabled="!isDraft" @input="updateCondition(index, { threshold: Number($event.target.value) })" />
              <button v-if="isDraft" type="button" class="icon-button" aria-label="删除条件" @click="removeCondition(index)"><PhTrash :size="15" /></button>
            </article>
            <button v-if="isDraft && draft.config.conditions.length < 4" type="button" class="add-condition" @click="addCondition"><PhPlus :size="15" />增加条件</button>
          </div>
          <p v-if="draft.config.conditions.length && !hasCountCondition" class="field-warning">至少需要一个数量条件。</p>
        </section>

        <section v-else class="rule-form-section rule-trial-section">
          <header><span>步骤 4 / 4</span><h3>试运行与发布</h3><p>只读查询真实历史 Alert，不会创建 Incident，也不会修改告警。</p></header>
          <div class="trial-controls"><label>历史范围<select v-model.number="historyHours" :disabled="!isDraft"><option :value="1">最近 1 小时</option><option :value="6">最近 6 小时</option><option :value="12">最近 12 小时</option><option :value="24">最近 24 小时</option><option :value="48">最近 48 小时</option></select></label><button type="button" class="button secondary" :disabled="!currentRule || !isDraft || isBusy" @click="emit('dry-run', historyHours)"><PhFlask :size="16" />{{ isBusy ? "正在试运行" : "开始试运行" }}</button></div>
          <div v-if="!dryRunResult" class="trial-empty"><PhFlask :size="26" /><strong>还没有试运行结果</strong><span>先保存当前草稿，再用真实历史告警验证。</span></div>
          <div v-else class="trial-result"><header><div><span>最近结果</span><strong>扫描 {{ dryRunResult.scanned_alert_count }} 条告警，命中 {{ dryRunResult.match_count }} 个窗口</strong></div><span :class="['trial-status', { warning: dryRunResult.truncated }]">{{ dryRunResult.truncated ? "结果已截断" : "查询完成" }}</span></header><p v-if="dryRunResult.truncated" class="field-warning">查询达到安全上限，请缩小历史范围或收紧规则后重新试运行。</p><div v-if="dryRunResult.matches?.length" class="trial-match-list"><article v-for="(match, index) in dryRunResult.matches" :key="`${match.group_key}-${index}`"><strong>{{ match.group_display_name }}</strong><span>{{ match.alert_count }} 条告警 · {{ match.distinct_alert_names }} 个 Alertname · 最高 {{ severityLabels[match.highest_severity] }}</span></article></div><p v-else>当前历史范围没有命中窗口；规则仍可发布，但建议先确认范围是否合理。</p></div>
          <div class="phase-boundary"><PhWarning :size="17" /><span><strong>当前阶段边界</strong>发布规则不会在本阶段自动创建 Incident。候选 Incident 的生成和去重将在下一阶段单独设计。</span></div>
        </section>
      </main>

      <aside class="rule-summary-panel">
        <span>规则说明</span><h3>{{ draft.name || "尚未命名" }}</h3>
        <p v-if="currentRule?.summary">{{ currentRule.summary }}</p><p v-else>完成必填项并保存草稿后，平台会生成唯一的中文规则说明。</p>
        <dl><div><dt>环境</dt><dd>{{ draft.config.environment || "未设置" }}</dd></div><div><dt>聚合</dt><dd>{{ draft.config.group_by === "SERVICE" ? "同一服务" : "同一实体" }}</dd></div><div><dt>窗口</dt><dd>{{ draft.config.window_minutes }} 分钟</dd></div><div><dt>条件关系</dt><dd>全部满足（AND）</dd></div></dl>
        <ol><li v-for="text in conditionPreview" :key="text">{{ text }}</li><li v-if="!conditionPreview.length">尚未添加触发条件</li></ol>
      </aside>
    </div>

    <footer class="rule-wizard-footer">
      <div><button v-if="currentRule?.state === 'DRAFT'" type="button" class="text-danger" :disabled="isBusy" @click="emit('remove')"><PhTrash :size="15" />删除草稿</button><button v-if="currentRule && currentRule.state !== 'DRAFT'" type="button" class="button secondary" :disabled="isBusy" @click="emit('copy', `${draft.name} 副本`)"><PhCopy :size="15" />复制为草稿</button><button v-if="currentRule?.state === 'PUBLISHED'" type="button" class="button secondary" :disabled="isBusy" @click="emit('disable')"><PhPause :size="15" />停用规则</button></div>
      <div><button v-if="step > 1" type="button" class="button secondary" :disabled="isBusy" @click="emit('step-change', step - 1)">上一步</button><button v-if="isDraft && step < 4" type="button" class="button secondary" :disabled="!complete || isBusy" @click="emit('save')">保存草稿</button><button v-if="step < 4" type="button" class="button primary" :disabled="(step === 1 && !draft.name.trim()) || (step === 2 && !draft.config.environment.trim()) || (step === 3 && !complete) || isBusy" @click="next">{{ step === 3 ? "下一步：试运行" : "下一步" }}</button><button v-else data-testid="publish-rule" type="button" class="button primary" :disabled="!currentRule?.publishable || dryRunResult?.truncated || isBusy || currentRule?.state !== 'DRAFT'" @click="emit('publish')">发布规则</button></div>
    </footer>
  </section>
</template>
