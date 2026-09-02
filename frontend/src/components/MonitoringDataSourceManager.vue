<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import {
  PhArrowsClockwise,
  PhCheckCircle,
  PhDatabase,
  PhFloppyDisk,
  PhPencilSimple,
  PhPlus,
  PhPulse,
  PhWarningCircle,
} from "@phosphor-icons/vue";

import {
  createMonitoringDataSource,
  fetchMonitoringDataSources,
  testMonitoringDataSource,
  updateMonitoringDataSource,
} from "../api/monitoringDataSources";

const sources = ref([]);
const state = ref("loading");
const error = ref("");
const busyId = ref(null);
const editorOpen = ref(false);
const saving = ref(false);
const operationMessage = ref("");
let controller;

const emptyDraft = () => ({
  id: "",
  version: 0,
  name: "",
  environment: "testing",
  source_type: "PROMETHEUS",
  base_url: "",
  credential_env_key: "",
  verify_tls: true,
  enabled: true,
  index: "logs-*",
  timestamp: "@timestamp",
  service: "service.name",
  environment_field: "environment",
  message: "message",
  trace_id: "trace.id",
  graphql_path: "/graphql",
});
const draft = reactive(emptyDraft());
const editorTitle = computed(() => draft.id ? "编辑监控数据源" : "新增监控数据源");

const typeLabels = {
  PROMETHEUS: "Prometheus",
  ELASTICSEARCH: "Elasticsearch",
  SKYWALKING: "SkyWalking",
};
const environmentLabels = {
  production: "生产环境",
  staging: "预发环境",
  testing: "测试环境",
  development: "开发环境",
};
const errorLabels = {
  monitoring_credential_missing: "服务端尚未配置凭据环境变量",
  monitoring_protocol_incompatible: "地址可访问，但返回内容不是对应的监控系统",
  network_timeout: "连接超时",
  network_unavailable: "无法连接到目标地址",
  http_authentication_failed: "身份认证失败",
  http_rate_limited: "监控系统请求受限",
  http_server_error: "监控系统暂时异常",
  http_client_error: "监控地址或权限配置错误",
  response_too_large: "检测响应超过安全限制",
};

function connectionLabel(source) {
  if (!source.last_test_state) return "尚未检测";
  return source.last_test_state === "AVAILABLE" ? "连接正常" : "连接失败";
}
function connectionDetail(source) {
  if (!source.last_test_state) return "保存后执行一次连接检测";
  if (source.last_test_state === "AVAILABLE") {
    const details = [];
    if (source.last_compatible_version) details.push(`版本 ${source.last_compatible_version}`);
    if (source.last_test_latency_ms !== null) details.push(`${source.last_test_latency_ms} ms`);
    return details.join(" · ") || "只读接口响应正常";
  }
  return errorLabels[source.last_test_error_code] ?? "连接检测未通过";
}
function formatTime(value) {
  if (!value) return "尚未检测";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

async function load() {
  controller?.abort();
  controller = new AbortController();
  state.value = "loading";
  error.value = "";
  try {
    const response = await fetchMonitoringDataSources({ signal: controller.signal });
    sources.value = response.items;
    state.value = sources.value.length ? "ready" : "empty";
  } catch (reason) {
    if (reason?.name === "AbortError") return;
    state.value = "error";
    error.value = reason?.userMessage ?? "监控数据源暂时无法读取";
  }
}
function resetDraft() {
  Object.assign(draft, emptyDraft());
}
function openCreate() {
  resetDraft();
  operationMessage.value = "";
  editorOpen.value = true;
}
function openEdit(source) {
  const mapping = source.field_mapping ?? {};
  Object.assign(draft, emptyDraft(), source, {
    credential_env_key: source.credential_env_key ?? "",
    index: mapping.index ?? "logs-*",
    timestamp: mapping.timestamp ?? "@timestamp",
    service: mapping.service ?? "service.name",
    environment_field: mapping.environment ?? "environment",
    message: mapping.message ?? "message",
    trace_id: mapping.trace_id ?? "trace.id",
    graphql_path: mapping.graphql_path ?? "/graphql",
  });
  operationMessage.value = "";
  editorOpen.value = true;
}
function fieldMapping() {
  if (draft.source_type === "ELASTICSEARCH") {
    return {
      index: draft.index.trim(),
      timestamp: draft.timestamp.trim(),
      service: draft.service.trim(),
      environment: draft.environment_field.trim(),
      message: draft.message.trim(),
      trace_id: draft.trace_id.trim(),
    };
  }
  if (draft.source_type === "SKYWALKING") return { graphql_path: draft.graphql_path.trim() };
  return {};
}
function command() {
  return {
    name: draft.name.trim(),
    environment: draft.environment.trim(),
    source_type: draft.source_type,
    base_url: draft.base_url.trim(),
    credential_env_key: draft.credential_env_key.trim() || null,
    field_mapping: fieldMapping(),
    verify_tls: draft.verify_tls,
    enabled: draft.enabled,
  };
}
async function save() {
  if (!draft.name.trim() || !draft.environment.trim() || !draft.base_url.trim()) {
    error.value = "请填写名称、环境和访问地址";
    return;
  }
  saving.value = true;
  error.value = "";
  try {
    if (draft.id) {
      await updateMonitoringDataSource(draft.id, {
        ...command(),
        expected_version: draft.version,
      });
    } else {
      await createMonitoringDataSource(command());
    }
    editorOpen.value = false;
    operationMessage.value = "监控数据源已保存";
    await load();
  } catch (reason) {
    error.value = reason?.userMessage ?? "监控数据源未保存";
  } finally {
    saving.value = false;
  }
}
async function runTest(source) {
  busyId.value = source.id;
  error.value = "";
  try {
    const result = await testMonitoringDataSource(source.id);
    operationMessage.value = result.state === "AVAILABLE" ? "连接检测通过" : "连接检测未通过";
    await load();
  } catch (reason) {
    error.value = reason?.userMessage ?? "连接检测未完成";
  } finally {
    busyId.value = null;
  }
}
async function toggle(source) {
  busyId.value = source.id;
  error.value = "";
  try {
    await updateMonitoringDataSource(source.id, {
      name: source.name,
      environment: source.environment,
      source_type: source.source_type,
      base_url: source.base_url,
      credential_env_key: source.credential_env_key,
      field_mapping: source.field_mapping,
      verify_tls: source.verify_tls,
      enabled: !source.enabled,
      expected_version: source.version,
    });
    operationMessage.value = source.enabled ? "监控数据源已停用" : "监控数据源已启用";
    await load();
  } catch (reason) {
    error.value = reason?.userMessage ?? "状态修改未完成";
  } finally {
    busyId.value = null;
  }
}

onMounted(load);
onBeforeUnmount(() => controller?.abort());
</script>

<template>
  <section data-testid="monitoring-source-manager" class="monitoring-source-page">
    <header class="monitoring-source-header">
      <div><strong>监控数据源</strong><span>为 Incident 提供指标、日志和链路的只读证据</span></div>
      <button data-testid="new-monitoring-source" type="button" class="button primary" @click="openCreate"><PhPlus :size="16" />新增数据源</button>
    </header>

    <div v-if="operationMessage" class="monitoring-operation-message"><PhCheckCircle :size="17" />{{ operationMessage }}</div>
    <div v-if="error" class="monitoring-operation-message error"><PhWarningCircle :size="17" />{{ error }}</div>

    <div v-if="state === 'loading'" class="monitoring-source-state"><PhArrowsClockwise :size="22" />正在读取监控数据源</div>
    <div v-else-if="state === 'error'" class="monitoring-source-state"><PhWarningCircle :size="22" />{{ error }}<button type="button" class="button secondary" @click="load">重新加载</button></div>
    <div v-else-if="state === 'empty'" class="monitoring-source-empty"><PhDatabase :size="34" /><strong>还没有监控数据源</strong><span>先配置测试环境的 Prometheus、Elasticsearch 或 SkyWalking</span><button type="button" class="button primary" @click="openCreate">新增第一个数据源</button></div>
    <div v-else class="monitoring-source-grid">
      <article v-for="source in sources" :key="source.id" class="monitoring-source-card">
        <header>
          <div class="monitoring-source-icon"><PhPulse :size="20" /></div>
          <div><strong>{{ source.name }}</strong><span>{{ typeLabels[source.source_type] }} · {{ environmentLabels[source.environment] ?? source.environment }}</span></div>
          <span :class="['monitoring-enabled-badge', { disabled: !source.enabled }]">{{ source.enabled ? '已启用' : '已停用' }}</span>
        </header>
        <div :class="['monitoring-connection', { available: source.last_test_state === 'AVAILABLE', failed: source.last_test_state === 'UNAVAILABLE' }]">
          <span>{{ connectionLabel(source) }}</span><strong>{{ connectionDetail(source) }}</strong><small>最近检测：{{ formatTime(source.last_tested_at) }}</small>
        </div>
        <dl><div><dt>访问地址</dt><dd>{{ source.base_url }}</dd></div><div><dt>凭据</dt><dd>{{ source.credential_env_key ? (source.credential_configured ? '环境变量已配置' : '环境变量缺失') : '无需凭据' }}</dd></div></dl>
        <footer>
          <button type="button" class="button secondary" :disabled="busyId === source.id" @click="openEdit(source)"><PhPencilSimple :size="15" />编辑</button>
          <button type="button" class="button secondary" :disabled="busyId === source.id" @click="toggle(source)">{{ source.enabled ? '停用' : '启用' }}</button>
          <button :data-testid="`test-source-${source.id}`" type="button" class="button primary" :disabled="busyId === source.id" @click="runTest(source)"><PhArrowsClockwise :size="15" />{{ busyId === source.id ? '检测中' : '检测连接' }}</button>
        </footer>
      </article>
    </div>

    <div v-if="editorOpen" class="dialog-backdrop" @click.self="editorOpen = false">
      <form class="operation-dialog monitoring-source-dialog" @submit.prevent="save">
        <header><div><span>只保存连接配置，不保存密钥值</span><h3>{{ editorTitle }}</h3></div><button type="button" aria-label="关闭" @click="editorOpen = false">×</button></header>
        <div class="monitoring-form-grid">
          <label>名称<input v-model="draft.name" name="name" maxlength="128" placeholder="测试 Prometheus" /></label>
          <label>环境<input v-model="draft.environment" name="environment" maxlength="32" placeholder="testing" /></label>
          <label>类型<select v-model="draft.source_type" name="source_type"><option value="PROMETHEUS">Prometheus</option><option value="ELASTICSEARCH">Elasticsearch</option><option value="SKYWALKING">SkyWalking</option></select></label>
          <label class="wide">访问地址<input v-model="draft.base_url" name="base_url" maxlength="2000" placeholder="http://127.0.0.1:9090" /></label>
          <label class="wide">凭据环境变量名称<input v-model="draft.credential_env_key" name="credential_env_key" maxlength="123" placeholder="可选，例如 II_PROMETHEUS_TOKEN" /><small>这里只填写变量名称，密钥值由后端运行环境提供。</small></label>
          <template v-if="draft.source_type === 'ELASTICSEARCH'"><label>日志索引<input v-model="draft.index" name="index" /></label><label>时间字段<input v-model="draft.timestamp" name="timestamp" /></label><label>服务字段<input v-model="draft.service" name="service" /></label><label>环境字段<input v-model="draft.environment_field" name="environment_field" /></label><label>消息字段<input v-model="draft.message" name="message" /></label><label>Trace ID 字段<input v-model="draft.trace_id" name="trace_id" /></label></template>
          <label v-if="draft.source_type === 'SKYWALKING'" class="wide">GraphQL 路径<input v-model="draft.graphql_path" name="graphql_path" /></label>
        </div>
        <div class="monitoring-switches"><label><input v-model="draft.verify_tls" type="checkbox" />校验证书</label><label><input v-model="draft.enabled" type="checkbox" />保存后启用</label></div>
        <footer><button type="button" class="button secondary" @click="editorOpen = false">取消</button><button type="submit" class="button primary" :disabled="saving"><PhFloppyDisk :size="16" />{{ saving ? '保存中' : '保存' }}</button></footer>
      </form>
    </div>
  </section>
</template>
