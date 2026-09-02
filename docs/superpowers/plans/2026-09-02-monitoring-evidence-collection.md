# Incident 监控数据自动取证实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 本仓库明确禁止子 Agent、功能分支和 Git worktree，所有步骤由当前会话在 `main` 分支顺序执行。

**Goal:** 为正式 Incident 增加 Prometheus、ELK、SkyWalking 的异步自动取证和人工重新取证能力，并按已确认的第三版页面集中展示标准化证据。

**Architecture:** Incident 创建事务只写 EvidenceRun 与持久化任务，独立 Worker 使用版本化只读取证包调用三个可注入适配器。各数据源结果经过有界标准化和确定性跨源关联后保存为 EvidenceItem；数据源失败不阻塞 Incident 运营，前端以关键发现为主线并保留按数据源查看入口。

**Tech Stack:** Python 3.13、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、MySQL 8.4、urllib 可注入传输层、Vue 3、Vitest。

**Spec:** `docs/superpowers/specs/2026-09-02-monitoring-evidence-collection-design.md`

## Global Constraints

- 所有变更直接在 `main` 分支完成，不创建功能分支、子 Agent 或 Git worktree。
- 第一版每个环境、每种监控类型最多启用一个数据源，只实现测试环境一套 Prometheus、Elasticsearch、SkyWalking。
- 自动取证只在 Incident 首次创建时生成；追加 Alert 不自动重复取证。
- 基线窗口固定为锚点前 30 至前 10 分钟，故障窗口为锚点前 10 分钟至运行开始，总查询范围不超过两小时。
- 所有外部查询必须来自仓库内版本化预定义模板，API、AI 和用户输入都不能提交原始查询文本。
- Secret 只从运行环境读取，不进入数据库、源码、日志、API、测试数据或证据。
- `scenario_id`、`scenario_version`、`experiment_id`、注入动作和标准答案不得进入取证上下文、查询参数、证据或报告。
- 单项默认超时 10 秒，整次运行硬上限 120 秒，临时失败最多尝试五次。
- Prometheus 单证据最多 20 个序列、每序列 240 个点；ELK 最多 50 条日志样本、单条 4,000 字；SkyWalking 最多 20 条 Trace、每条 100 个关键 Span。
- 单个数据源失败时保存其他成功证据并把运行标记为 `PARTIAL`；取证失败不得阻塞 Alert 接收、Incident 状态操作和飞书协同。
- 每个任务先写失败测试，再做最小完整实现；每次提交只包含本任务文件，保留当前工作区所有已有改动。

---

## 文件结构

新增后端文件按责任拆分：

- `domain/evidence.py`：取证状态、窗口、上下文、运行与证据领域对象；
- `domain/evidence_packs.py`：版本化取证包、模板与匹配规则；
- `adapters/monitoring_http.py`：有界 HTTP 传输协议与 urllib 实现；
- `adapters/prometheus.py`、`elasticsearch.py`、`skywalking.py`：三个只读适配器；
- `services/evidence_planning.py`：从 Incident/Alert 生成执行计划；
- `services/evidence_normalization.py`：结果上限、脱敏和标准输出；
- `services/evidence_correlation.py`：确定性跨源关联；
- `services/evidence_collection.py`：单次任务编排；
- `services/evidence_collection_runner.py`：租约、重试和批处理；
- `services/monitoring_data_sources.py`：数据源配置与连通测试；
- `services/incident_evidence.py`：运行历史、详情和人工重新取证；
- `persistence/evidence_repository.py`：五张新表的读写；
- `api/routes/monitoring_data_sources.py`、`incident_evidence.py` 及对应 schema：HTTP 边界；
- `frontend/src/api/incidentEvidence.js`、`composables/useIncidentEvidence.js`、`presentation/evidenceView.js`：前端数据层；
- `frontend/src/components/IncidentEvidence.vue`、`EvidenceFinding.vue`：第三版取证页面。

---

### Task 1: 取证领域模型与时间窗口

**Files:**
- Create: `backend/src/incident_intelligence/domain/evidence.py`
- Modify: `backend/src/incident_intelligence/domain/incidents.py`
- Modify: `backend/src/incident_intelligence/ids.py`
- Test: `backend/tests/unit/domain/test_evidence.py`
- Test: `backend/tests/unit/domain/test_incidents.py`

**Interfaces:**
- Consumes: `incident_intelligence.domain.models.UtcAwareDatetime`。
- Produces: `EvidenceContext`、`EvidenceWindow`、`EvidenceRun`、`EvidenceItem`、`build_evidence_window(anchor_at, run_started_at)`、`summarize_run_status(items)`。

- [ ] **Step 1: 写窗口、状态汇总和字段边界的失败测试**

```python
def test_build_evidence_window_uses_fixed_baseline_and_caps_total_range():
    anchor = datetime(2026, 9, 2, 6, 0, tzinfo=UTC)
    window = build_evidence_window(anchor, anchor + timedelta(hours=3))
    assert window.baseline_start == anchor - timedelta(minutes=30)
    assert window.baseline_end == anchor - timedelta(minutes=10)
    assert window.fault_start == anchor - timedelta(minutes=10)
    assert window.fault_end == anchor + timedelta(minutes=90)


def test_summarize_run_status_preserves_partial_success():
    assert summarize_run_status(("SUCCEEDED", "FAILED")) == "PARTIAL"
    assert summarize_run_status(("NO_DATA", "SUCCEEDED")) == "SUCCEEDED"
    assert summarize_run_status(("FAILED", "FAILED")) == "FAILED"
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/domain/test_evidence.py -v`

Expected: FAIL，提示无法导入 `incident_intelligence.domain.evidence`。

- [ ] **Step 3: 实现冻结领域对象和纯函数**

```python
EvidenceRunState = Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED"]
EvidenceItemState = Literal[
    "SUCCEEDED", "NO_DATA", "INSUFFICIENT_BASELINE", "MISSING_TARGET",
    "SKIPPED_DEPENDENCY", "FAILED",
]

class EvidenceWindow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    baseline_start: UtcAwareDatetime
    baseline_end: UtcAwareDatetime
    fault_start: UtcAwareDatetime
    fault_end: UtcAwareDatetime

def build_evidence_window(anchor_at: datetime, run_started_at: datetime) -> EvidenceWindow:
    capped_end = min(run_started_at, anchor_at + timedelta(minutes=90))
    return EvidenceWindow(
        baseline_start=anchor_at - timedelta(minutes=30),
        baseline_end=anchor_at - timedelta(minutes=10),
        fault_start=anchor_at - timedelta(minutes=10),
        fault_end=capped_end,
    )
```

同时验证所有时间为 UTC aware、service 最长 128 字、Alertname 集合最多 200 个、结果 JSON 只能使用标准证据类型。给 `IdPrefix` 增加 `mds`、`evr`、`evitem`、`evtask`、`evop`；给 Incident 活动增加 `EVIDENCE_COLLECTION_COMPLETED`、`EVIDENCE_COLLECTION_PARTIAL`、`EVIDENCE_COLLECTION_FAILED` 三种确定性类型。

- [ ] **Step 4: 运行领域测试并确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/domain/test_evidence.py tests/unit/domain/test_incidents.py -v`

Expected: PASS。

- [ ] **Step 5: 提交领域模型**

```bash
git add backend/src/incident_intelligence/domain/evidence.py backend/src/incident_intelligence/domain/incidents.py backend/src/incident_intelligence/ids.py backend/tests/unit/domain/test_evidence.py backend/tests/unit/domain/test_incidents.py
git commit -m "feat: 定义 Incident 取证领域模型"
```

---

### Task 2: MySQL 取证表与仓储

**Files:**
- Create: `backend/migrations/versions/0019_monitoring_evidence_collection.py`
- Modify: `backend/src/incident_intelligence/persistence/models.py`
- Create: `backend/src/incident_intelligence/persistence/evidence_repository.py`
- Modify: `backend/src/incident_intelligence/persistence/unit_of_work.py`
- Test: `backend/tests/integration/persistence/test_monitoring_evidence_schema.py`
- Test: `backend/tests/integration/services/test_evidence_repository.py`

**Interfaces:**
- Consumes: Task 1 的 `EvidenceRun`、`EvidenceItem`。
- Produces: `MonitoringDataSourceRepository`、`EvidenceRunRepository`、`EvidenceTaskRepository`、`EvidenceOperationRepository`。

- [ ] **Step 1: 写迁移结构和仓储并发边界失败测试**

```python
def test_schema_has_evidence_tables_and_unique_enabled_source(migrated_engine):
    inspector = inspect(migrated_engine)
    assert {
        "monitoring_data_sources", "incident_evidence_runs", "incident_evidence_items",
        "evidence_collection_tasks", "evidence_collection_operations",
    } <= set(inspector.get_table_names())


def test_auto_run_is_unique_per_incident(session):
    repository = EvidenceRunRepository(session)
    repository.insert(make_run(id="evr_1", trigger="AUTOMATIC"))
    with pytest.raises(IntegrityError):
        repository.insert(make_run(id="evr_2", trigger="AUTOMATIC"))
```

- [ ] **Step 2: 运行定向集成测试并确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/persistence/test_monitoring_evidence_schema.py tests/integration/services/test_evidence_repository.py -v`

Expected: FAIL，缺少迁移和仓储。

- [ ] **Step 3: 创建 0019 迁移和 SQLAlchemy Row**

迁移从 `0018_restore_system_sources` 向前，创建五张表，并重建 `operational_incident_activities` 的 kind 检查约束以允许 Task 1 的三种取证活动。关键数据库约束必须直接落在 MySQL：

```python
sa.UniqueConstraint("environment", "source_type", "enabled_slot", name="monitoring_source_enabled")
sa.UniqueConstraint("incident_id", "automatic_slot", name="incident_auto_evidence_run")
sa.UniqueConstraint("evidence_run_id", "evidence_key", name="evidence_item_key")
sa.UniqueConstraint("scope", "idempotency_key_hash", name="evidence_operation_key")
sa.CheckConstraint("attempt_count >= 0 AND attempt_count <= 5", name="evidence_task_attempts")
```

`enabled_slot` 仅在启用时保存固定值 `1`，停用时为 `NULL`；`automatic_slot` 仅自动运行保存 `1`。所有表使用 InnoDB、`utf8mb4_bin`、UTC 微秒时间和有界 JSON/Text。

- [ ] **Step 4: 实现仓储及租约原子操作**

```python
class EvidenceTaskRepository:
    def claim_due(self, task_id: str, *, owner: str, now: datetime, lease_until: datetime) -> bool:
        result = self._session.execute(
            update(EvidenceCollectionTaskRow)
            .where(EvidenceCollectionTaskRow.id == task_id)
            .where(EvidenceCollectionTaskRow.state == "PENDING")
            .where(EvidenceCollectionTaskRow.next_attempt_at <= now)
            .values(state="LEASED", lease_owner=owner, lease_until=lease_until, updated_at=now)
        )
        return result.rowcount == 1

class EvidenceRunRepository:
    def append_item(self, item: EvidenceItem) -> None:
        self._session.add(EvidenceItemRow.from_domain(item))
        self._session.flush()

    def list_items(self, run_id: str, *, limit: int = 101) -> tuple[EvidenceItem, ...]:
        rows = self._session.scalars(
            select(EvidenceItemRow)
            .where(EvidenceItemRow.evidence_run_id == run_id)
            .order_by(EvidenceItemRow.created_at, EvidenceItemRow.id)
            .limit(limit)
        )
        return tuple(row.to_domain() for row in rows)
```

同一文件以相同条件更新方式实现 `complete`、`reschedule`、`fail`、`insert_run_with_task` 和 `find_active`；每个方法的状态前置条件及 owner 校验由对应失败测试固定。

- [ ] **Step 5: 接入 UnitOfWork 并运行集成测试**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/persistence/test_monitoring_evidence_schema.py tests/integration/services/test_evidence_repository.py -v`

Expected: PASS，重复自动运行、重复证据键和租约丢失都由数据库或条件更新拒绝。

- [ ] **Step 6: 提交迁移和仓储**

```bash
git add backend/migrations/versions/0019_monitoring_evidence_collection.py backend/src/incident_intelligence/persistence/models.py backend/src/incident_intelligence/persistence/evidence_repository.py backend/src/incident_intelligence/persistence/unit_of_work.py backend/tests/integration/persistence/test_monitoring_evidence_schema.py backend/tests/integration/services/test_evidence_repository.py
git commit -m "feat: 持久化 Incident 监控取证"
```

---

### Task 3: 监控数据源配置与安全凭据引用

**Files:**
- Create: `backend/src/incident_intelligence/services/monitoring_data_sources.py`
- Create: `backend/src/incident_intelligence/api/schemas/monitoring_data_sources.py`
- Create: `backend/src/incident_intelligence/api/routes/monitoring_data_sources.py`
- Modify: `backend/src/incident_intelligence/api/dependencies.py`
- Modify: `backend/src/incident_intelligence/api/router.py`
- Modify: `backend/src/incident_intelligence/main.py`
- Test: `backend/tests/integration/services/test_monitoring_data_sources.py`
- Test: `backend/tests/api/test_monitoring_data_sources.py`

**Interfaces:**
- Consumes: Task 2 的 `MonitoringDataSourceRepository`。
- Produces: `MonitoringDataSourceService.list/create/update/test_connection`、`MonitoringCredentialResolver.resolve(source)`；写操作使用乐观版本，第一版不增加独立的数据源幂等表。

- [ ] **Step 1: 写环境隔离、凭据不回显和幂等写操作失败测试**

```python
def test_create_source_returns_credential_readiness_without_secret(client, auth_headers):
    response = client.post("/api/v1/monitoring-data-sources", headers=auth_headers, json={
        "name": "测试 Prometheus", "environment": "testing", "source_type": "PROMETHEUS",
        "base_url": "http://prometheus:9090", "credential_env_key": "II_PROMETHEUS_TOKEN",
        "field_mapping": {"service": "service", "environment": "environment"}, "enabled": True,
    })
    assert response.status_code == 201
    assert response.json()["credential_configured"] is False
    assert "token" not in response.text.casefold()
```

- [ ] **Step 2: 运行服务/API 测试并确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_monitoring_data_sources.py tests/api/test_monitoring_data_sources.py -v`

Expected: FAIL，路由和服务不存在。

- [ ] **Step 3: 实现服务、字段映射白名单和环境变量解析**

```python
class MonitoringCredentialResolver:
    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    def resolve(self, source: MonitoringDataSource) -> str | None:
        if source.credential_env_key is None:
            return None
        value = self._environ.get(source.credential_env_key, "").strip()
        return value or None

    def capability(self, source: MonitoringDataSource) -> CredentialCapability:
        missing = () if source.credential_env_key is None or self.resolve(source) else (source.credential_env_key,)
        return CredentialCapability(configured=not missing, missing_environment_keys=missing)

ALLOWED_FIELD_KEYS = {
    "PROMETHEUS": frozenset({"service", "environment"}),
    "ELASTICSEARCH": frozenset({"index", "timestamp", "service", "environment", "level", "message", "error_type", "trace_id", "host"}),
    "SKYWALKING": frozenset({"graphql_path"}),
}
```

拒绝包含用户信息、密码、query、fragment 或非 HTTP(S) scheme 的 URL；凭据引用必须匹配 `^II_[A-Z0-9_]{1,120}$`。

- [ ] **Step 4: 注册 API、依赖和应用服务**

实现 GET/POST/PATCH/test 四个端点。POST/PATCH/test 使用 Bearer Token；PATCH 使用期望版本。test 只返回 `AVAILABLE/UNAVAILABLE`、响应毫秒、兼容版本和安全错误码。

- [ ] **Step 5: 运行定向测试**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_monitoring_data_sources.py tests/api/test_monitoring_data_sources.py -v`

Expected: PASS。

- [ ] **Step 6: 提交数据源管理**

```bash
git add backend/src/incident_intelligence/services/monitoring_data_sources.py backend/src/incident_intelligence/api/schemas/monitoring_data_sources.py backend/src/incident_intelligence/api/routes/monitoring_data_sources.py backend/src/incident_intelligence/api/dependencies.py backend/src/incident_intelligence/api/router.py backend/src/incident_intelligence/main.py backend/tests/integration/services/test_monitoring_data_sources.py backend/tests/api/test_monitoring_data_sources.py
git commit -m "feat: 管理监控取证数据源"
```

---

### Task 4: 版本化取证包与确定性执行计划

**Files:**
- Create: `backend/src/incident_intelligence/domain/evidence_packs.py`
- Create: `backend/src/incident_intelligence/services/evidence_planning.py`
- Test: `backend/tests/unit/domain/test_evidence_packs.py`
- Test: `backend/tests/unit/services/test_evidence_planning.py`

**Interfaces:**
- Consumes: Task 1 的 `EvidenceContext`、`EvidenceWindow`。
- Produces: `EvidencePackRegistry.default()`、`EvidencePlanningService.plan(context, run_started_at) -> EvidenceExecutionPlan`。

- [ ] **Step 1: 写包选择、去重、缺少 service 和禁用身份失败测试**

```python
def test_plan_selects_common_http_and_mysql_without_duplicate_queries():
    plan = planner.plan(context(alert_names=("HighHttpErrorRate", "MySQLRowLockWaitActive")), now)
    assert plan.pack_versions == ("common-service:v1", "http:v1", "mysql:v1")
    assert len({item.execution_key for item in plan.items}) == len(plan.items)


@pytest.mark.parametrize("key", ["scenario_id", "scenario_version", "experiment_id"])
def test_context_rejects_forbidden_identity(key):
    with pytest.raises(ValidationError):
        EvidenceContext.model_validate({**valid_context(), "facts": {key: "forbidden"}})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/domain/test_evidence_packs.py tests/unit/services/test_evidence_planning.py -v`

Expected: FAIL，缺少 registry 与 planner。

- [ ] **Step 3: 实现冻结模板、Alertname 匹配和安全参数渲染**

```python
class EvidenceQueryTemplate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    version: int
    source_type: Literal["PROMETHEUS", "ELASTICSEARCH", "SKYWALKING"]
    evidence_type: EvidenceType
    query_name: str
    parameters: tuple[str, ...]
    threshold: EvidenceThreshold | None = None

class EvidencePackRegistry:
    def resolve(self, alert_names: tuple[str, ...], facts: Mapping[str, str]) -> tuple[EvidencePack, ...]:
        selected = [self._packs["common-service"]]
        selected.extend(
            pack for pack in self._packs.values()
            if pack.id != "common-service" and pack.matches(alert_names, facts)
        )
        return tuple(sorted(selected[:5], key=lambda pack: pack.priority))
```

模板保存查询名称和受控表达式，不接收 API 原始查询。HTTP/JVM/MySQL 匹配使用经测试的大小写不敏感 Alertname 前缀或正则，最多五个包、40 个计划项。

- [ ] **Step 4: 实现锚点退化和计划生成**

```python
def choose_anchor(alerts: tuple[IncidentAlertEvidenceFact, ...]) -> tuple[datetime, str]:
    started = tuple(item.episode_started_at for item in alerts if item.episode_started_at)
    if started:
        return min(started), "episode_started_at"
    return min(item.first_received_at for item in alerts), "first_received_at_fallback"
```

缺 service 的计划项直接生成 `MISSING_TARGET` 预结果；不依赖 service 的项仍进入执行列表。

- [ ] **Step 5: 运行 planner 测试并提交**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/domain/test_evidence_packs.py tests/unit/services/test_evidence_planning.py -v`

Expected: PASS。

```bash
git add backend/src/incident_intelligence/domain/evidence_packs.py backend/src/incident_intelligence/services/evidence_planning.py backend/tests/unit/domain/test_evidence_packs.py backend/tests/unit/services/test_evidence_planning.py
git commit -m "feat: 生成版本化取证执行计划"
```

---

### Task 5: 有界 HTTP 传输与 Prometheus 适配器

**Files:**
- Create: `backend/src/incident_intelligence/adapters/monitoring_http.py`
- Create: `backend/src/incident_intelligence/adapters/prometheus.py`
- Test: `backend/tests/unit/adapters/test_monitoring_http.py`
- Test: `backend/tests/unit/adapters/test_prometheus.py`

**Interfaces:**
- Consumes: Task 4 的 `EvidenceQueryRequest`。
- Produces: `MonitoringHttpTransport` 协议、`UrllibMonitoringTransport`、`PrometheusEvidenceAdapter.collect(request) -> AdapterEvidenceResult`。

- [ ] **Step 1: 写 POST 编码、状态分类、响应上限和矩阵解析失败测试**

```python
def test_prometheus_posts_range_query_with_bounded_limit(fake_transport):
    adapter.collect(prom_request(query="up{service=\"payment-api\"}"))
    sent = fake_transport.requests[0]
    assert sent.path == "/api/v1/query_range"
    assert sent.form["limit"] == "20"


def test_prometheus_rejects_more_than_20_series():
    with pytest.raises(MonitoringPermanentError, match="series_limit_exceeded"):
        adapter_with_response(matrix_response(series=21)).collect(prom_request())
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/adapters/test_monitoring_http.py tests/unit/adapters/test_prometheus.py -v`

Expected: FAIL，适配器不存在。

- [ ] **Step 3: 实现可注入传输层**

```python
class MonitoringHttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        form: Mapping[str, str] | None,
        json: Mapping[str, object] | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> MonitoringHttpResponse:
        raise NotImplementedError

class MonitoringRetryableError(Exception):
    pass

class MonitoringPermanentError(Exception):
    pass
```

urllib 实现最多读取 `max_response_bytes + 1`；网络错误、超时、429 和 5xx 为 retryable，鉴权、权限、非法 JSON 和其他 4xx 为 permanent。异常只携带最多 64 字的安全错误码。

- [ ] **Step 4: 实现 Prometheus query/query_range 解析**

只接受 planner 生成的请求，使用 form POST；验证 `status=success`、resultType、series/point 上限、数值有限性和 UTC 时间。NaN/Inf 记录为缺失点，不传入统计。

- [ ] **Step 5: 运行适配器测试并提交**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/adapters/test_monitoring_http.py tests/unit/adapters/test_prometheus.py -v`

Expected: PASS。

```bash
git add backend/src/incident_intelligence/adapters/monitoring_http.py backend/src/incident_intelligence/adapters/prometheus.py backend/tests/unit/adapters/test_monitoring_http.py backend/tests/unit/adapters/test_prometheus.py
git commit -m "feat: 采集 Prometheus 指标证据"
```

---

### Task 6: ELK 日志适配器与敏感信息清理

**Files:**
- Create: `backend/src/incident_intelligence/adapters/elasticsearch.py`
- Create: `backend/src/incident_intelligence/services/evidence_normalization.py`
- Test: `backend/tests/unit/adapters/test_elasticsearch.py`
- Test: `backend/tests/unit/services/test_evidence_normalization.py`

**Interfaces:**
- Consumes: Task 5 的 `MonitoringHttpTransport`、Task 4 的 ELK 请求。
- Produces: `ElasticsearchEvidenceAdapter.collect()`、`redact_log_message(text) -> str`、`normalize_log_result()`。

- [ ] **Step 1: 写字段白名单、50 条上限、指纹聚合和脱敏失败测试**

```python
def test_redacts_authorization_cookie_password_and_connection_string():
    text = "Authorization: Bearer abc Cookie: sid=secret password=hunter2 mysql://u:p@db/app"
    sanitized = redact_log_message(text)
    assert "abc" not in sanitized and "secret" not in sanitized and "hunter2" not in sanitized
    assert "mysql://u:p" not in sanitized


def test_elasticsearch_requests_only_whitelisted_source_fields(fake_transport):
    adapter.collect(elk_request())
    body = fake_transport.requests[0].json
    assert body["size"] == 50
    assert set(body["_source"]) == {"@timestamp", "service.name", "log.level", "message", "error.type", "trace.id", "host.name"}
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/adapters/test_elasticsearch.py tests/unit/services/test_evidence_normalization.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现固定 Query DSL 构造器**

```python
def build_log_query(request: ElasticsearchQueryRequest) -> dict[str, object]:
    return {
        "size": 50,
        "track_total_hits": True,
        "_source": list(request.source_fields),
        "query": {"bool": {"filter": [
            {"range": {request.timestamp_field: {"gte": request.start.isoformat(), "lt": request.end.isoformat()}}},
            {"term": {request.service_field: request.service}},
            {"term": {request.environment_field: request.environment}},
        ]}},
        "sort": [{request.timestamp_field: "desc"}],
    }
```

索引名、字段名只来自 Task 3 校验后的配置；service/environment 只能作为 term 值，不得拼入 DSL 字符串。

- [ ] **Step 4: 实现规范化、错误指纹和脱敏**

保留白名单字段；message 先统一空白、截断 4,000 字再脱敏。错误指纹使用清理后的 `error_type + normalized_message` SHA-256，不保存未脱敏指纹输入。

- [ ] **Step 5: 运行测试并提交**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/adapters/test_elasticsearch.py tests/unit/services/test_evidence_normalization.py -v`

Expected: PASS。

```bash
git add backend/src/incident_intelligence/adapters/elasticsearch.py backend/src/incident_intelligence/services/evidence_normalization.py backend/tests/unit/adapters/test_elasticsearch.py backend/tests/unit/services/test_evidence_normalization.py
git commit -m "feat: 采集并清理 ELK 日志证据"
```

---

### Task 7: SkyWalking 服务、端点和 Trace 适配器

**Files:**
- Create: `backend/src/incident_intelligence/adapters/skywalking.py`
- Test: `backend/tests/unit/adapters/test_skywalking.py`

**Interfaces:**
- Consumes: Task 5 的 `MonitoringHttpTransport`、Task 4 的 SkyWalking 请求。
- Produces: `SkyWalkingEvidenceAdapter.collect() -> AdapterEvidenceResult`。

- [ ] **Step 1: 写 GraphQL 变量、错误响应和 Trace/Span 上限失败测试**

```python
def test_skywalking_uses_variables_instead_of_interpolating_service(fake_transport):
    adapter.collect(sw_request(service='payment-api"} mutation { x }'))
    body = fake_transport.requests[0].json
    assert "$service" in body["query"]
    assert body["variables"]["service"] == 'payment-api"} mutation { x }'


def test_skywalking_caps_trace_and_span_results():
    result = adapter_with_response(trace_response(traces=25, spans=130)).collect(sw_request())
    assert len(result.traces) == 20
    assert all(len(trace.spans) <= 100 for trace in result.traces)
    assert result.truncated is True
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/adapters/test_skywalking.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现固定 GraphQL 文档和变量解析**

```python
class SkyWalkingEvidenceAdapter:
    def collect(self, request: SkyWalkingQueryRequest) -> AdapterEvidenceResult:
        payload = {"query": QUERIES[request.query_name], "variables": request.variables()}
        response = self._transport.request("POST", request.url, headers=request.headers, form=None, json=payload, timeout_seconds=request.timeout_seconds, max_response_bytes=1_048_576)
        return parse_skywalking_response(request, response.body)
```

固定查询覆盖服务指标、端点排行、依赖排行和 Trace 摘要；GraphQL `errors` 数组转换成安全 permanent/retryable 错误，不保存服务端堆栈。

- [ ] **Step 4: 运行测试并提交**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/adapters/test_skywalking.py -v`

Expected: PASS。

```bash
git add backend/src/incident_intelligence/adapters/skywalking.py backend/tests/unit/adapters/test_skywalking.py
git commit -m "feat: 采集 SkyWalking 链路证据"
```

---

### Task 8: 确定性跨源关联

**Files:**
- Create: `backend/src/incident_intelligence/services/evidence_correlation.py`
- Test: `backend/tests/unit/services/test_evidence_correlation.py`

**Interfaces:**
- Consumes: 三个适配器的标准结果。
- Produces: `EvidenceCorrelationService.correlate(context, items) -> tuple[EvidenceItemDraft, ...]`。

- [ ] **Step 1: 写 Trace ID/时间重叠、依赖缺失和禁止根因措辞测试**

```python
def test_correlates_trace_logs_only_when_service_time_and_trace_id_match():
    items = service.correlate(context, (trace_item("t-1"), log_item("t-1")))
    assert items[0].result["matched_trace_count"] == 1
    assert items[0].meaning == "失败 Trace 在同服务、同时间窗口找到对应日志"


def test_missing_skywalking_marks_dependency_skipped_not_normal():
    item = service.correlate(context, (failed_item("SKYWALKING"), log_item("t-1")))[0]
    assert item.state == "SKIPPED_DEPENDENCY"
    assert "正常" not in item.meaning and "根因" not in item.meaning
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/services/test_evidence_correlation.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现纯规则关联器**

```python
FORBIDDEN_CONCLUSION_WORDS = ("根因", "置信度", "自动修复", "确定导致")

def same_scope(left: EvidenceSignal, right: EvidenceSignal) -> bool:
    return left.environment == right.environment and left.service == right.service and left.start < right.end and right.start < left.end
```

只输出指标/端点时间重叠、Trace ID 与日志匹配、日志异常但链路缺失三种模板化事实；含禁用结论词的模板在 registry 启动校验时失败。

- [ ] **Step 4: 运行测试并提交**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/services/test_evidence_correlation.py -v`

Expected: PASS。

```bash
git add backend/src/incident_intelligence/services/evidence_correlation.py backend/tests/unit/services/test_evidence_correlation.py
git commit -m "feat: 关联指标日志与链路证据"
```

---

### Task 9: 取证编排、Worker 与 Incident 自动任务

**Files:**
- Create: `backend/src/incident_intelligence/services/evidence_collection.py`
- Create: `backend/src/incident_intelligence/services/evidence_collection_runner.py`
- Modify: `backend/src/incident_intelligence/services/incident_evaluation.py`
- Modify: `backend/src/incident_intelligence/main.py`
- Modify: `backend/src/incident_intelligence/settings.py`
- Test: `backend/tests/integration/services/test_evidence_collection.py`
- Test: `backend/tests/unit/services/test_evidence_collection_runner.py`
- Test: `backend/tests/integration/test_incident_auto_evidence.py`

**Interfaces:**
- Consumes: Tasks 2–8 的仓储、planner、适配器和关联器。
- Produces: `EvidenceCollectionService.process(task_id)`、`EvidenceCollectionRunner.run_once(limit)`。

- [ ] **Step 1: 写自动任务同事务、部分成功、租约重领和不重复自动取证测试**

```python
def test_created_incident_and_auto_evidence_task_commit_together(service, repositories):
    result = service.process("iej_1")
    run = repositories.evidence_runs.find_auto(result.incident_ids[0])
    assert run is not None and run.state == "QUEUED"


def test_added_alert_does_not_create_second_auto_run(service, existing_incident):
    service.process("iej_new_alert")
    assert repositories.evidence_runs.count_auto(existing_incident.id) == 1
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_evidence_collection.py tests/unit/services/test_evidence_collection_runner.py tests/integration/test_incident_auto_evidence.py -v`

Expected: FAIL。

- [ ] **Step 3: 在 Incident 新建事务中入队自动取证**

在 `IncidentEvaluationService._apply_match()` 的 `was_created=True` 分支、Incident 与 Alert 关联落库后调用：

```python
evidence_runs.enqueue_automatic(
    incident_id=change.incident.id,
    run_id=self._id_factory("evr"),
    task_id=self._id_factory("evtask"),
    anchor_alert_ids=change.new_alert_ids,
    now=now,
)
```

既有 Incident 分支不得调用该接口。

- [ ] **Step 4: 实现 collection service 和 runner**

service 领取任务后生成 plan，逐项执行并立即写不可变 EvidenceItem；成功项通过唯一键跳过。完成基础项后执行 correlation，汇总 Run 状态并追加一次 Incident 活动。runner 复用现有 Incident worker 的租约、指数退避和批次模式。

- [ ] **Step 5: 注册设置与生命周期任务**

```python
evidence_worker_enabled: bool = True
evidence_worker_poll_seconds: int = Field(default=2, ge=1, le=60)
evidence_worker_lease_seconds: int = Field(default=60, ge=10, le=300)
evidence_worker_max_attempts: int = Field(default=5, ge=1, le=5)
evidence_worker_batch_size: int = Field(default=10, ge=1, le=50)
```

应用关闭时与现有 Worker 一样等待当前批次结束；runner 异常只记录安全错误码和服务端日志，不让应用退出。

- [ ] **Step 6: 运行定向测试并提交**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_evidence_collection.py tests/unit/services/test_evidence_collection_runner.py tests/integration/test_incident_auto_evidence.py -v`

Expected: PASS。

```bash
git add backend/src/incident_intelligence/services/evidence_collection.py backend/src/incident_intelligence/services/evidence_collection_runner.py backend/src/incident_intelligence/services/incident_evaluation.py backend/src/incident_intelligence/main.py backend/src/incident_intelligence/settings.py backend/tests/integration/services/test_evidence_collection.py backend/tests/unit/services/test_evidence_collection_runner.py backend/tests/integration/test_incident_auto_evidence.py
git commit -m "feat: 异步执行 Incident 自动取证"
```

---

### Task 10: EvidenceRun API、人工重新取证与健康摘要

**Files:**
- Create: `backend/src/incident_intelligence/services/incident_evidence.py`
- Create: `backend/src/incident_intelligence/api/schemas/incident_evidence.py`
- Create: `backend/src/incident_intelligence/api/routes/incident_evidence.py`
- Modify: `backend/src/incident_intelligence/api/dependencies.py`
- Modify: `backend/src/incident_intelligence/api/router.py`
- Modify: `backend/src/incident_intelligence/services/incidents.py`
- Modify: `backend/src/incident_intelligence/api/schemas/incidents.py`
- Modify: `backend/src/incident_intelligence/api/routes/health.py`
- Modify: `backend/src/incident_intelligence/main.py`
- Test: `backend/tests/integration/services/test_incident_evidence.py`
- Test: `backend/tests/api/test_incident_evidence.py`
- Test: `backend/tests/api/test_health.py`

**Interfaces:**
- Consumes: Task 9 的 EvidenceRun 与任务。
- Produces: `IncidentEvidenceService.list_runs/get_run/request_manual`，三个 Incident 取证端点和健康摘要。

- [ ] **Step 1: 写历史读取、人工幂等、并发运行冲突和安全健康响应失败测试**

```python
def test_manual_evidence_replay_returns_original_run(client, auth_headers):
    headers = {**auth_headers, "Idempotency-Key": "manual-evidence-1"}
    first = client.post(f"/api/v1/incidents/{INCIDENT_ID}/evidence-runs", headers=headers)
    replay = client.post(f"/api/v1/incidents/{INCIDENT_ID}/evidence-runs", headers=headers)
    assert replay.json()["run"]["id"] == first.json()["run"]["id"]
    assert replay.json()["replayed"] is True


def test_health_does_not_expose_source_url_query_or_trace_id(client):
    body = client.get("/health").text
    assert "http://prometheus" not in body and "query" not in body.casefold() and "trace" not in body.casefold()
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_incident_evidence.py tests/api/test_incident_evidence.py tests/api/test_health.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现查询服务和人工取证操作**

```python
class IncidentEvidenceService:
    def request_manual(
        self,
        incident_id: str,
        *,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> EvidenceRunMutationResult:
        key_hash = sha256(idempotency_key.encode()).hexdigest()
        with self._uow_factory() as uow:
            prior = uow.evidence_operations.find(f"incident:{incident_id}:evidence", key_hash)
            if prior is not None:
                return EvidenceRunMutationResult(run=uow.evidence_runs.require(prior.run_id), replayed=True)
            if uow.evidence_runs.find_active(incident_id, for_update=True) is not None:
                raise EvidenceRunAlreadyActive()
            run = self._enqueue_manual(uow, incident_id, actor=actor, request_id=request_id)
            uow.evidence_operations.record(run, key_hash=key_hash)
            uow.commit()
            return EvidenceRunMutationResult(run=run, replayed=False)
```

同一服务的 `list_runs(incident_id, limit=50, offset=0)` 只返回有界运行摘要，`get_run(incident_id, run_id)` 最多读取 101 项以标记截断。

人工请求先锁 Incident，再锁当前 active run；存在 active run 时返回 409 和当前 run ID。精确幂等重放优先于 active 冲突判断。

- [ ] **Step 4: 注册 API 并给 Incident 详情增加最新摘要**

详情只返回 `latest_evidence_run: {id,state,success_count,missing_count,failed_count,completed_at} | null`。完整 items 只由 run 详情端点返回，最多 100 项。

- [ ] **Step 5: 扩展健康摘要**

增加 evidence worker 计数和每类启用来源的 `configured/last_connection_state`；数据库不可用仍保持现有 503。取证降级不改变 `/health/ready` 的数据库 readiness。

- [ ] **Step 6: 运行测试并提交**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_incident_evidence.py tests/api/test_incident_evidence.py tests/api/test_health.py -v`

Expected: PASS。

```bash
git add backend/src/incident_intelligence/services/incident_evidence.py backend/src/incident_intelligence/api/schemas/incident_evidence.py backend/src/incident_intelligence/api/routes/incident_evidence.py backend/src/incident_intelligence/api/dependencies.py backend/src/incident_intelligence/api/router.py backend/src/incident_intelligence/services/incidents.py backend/src/incident_intelligence/api/schemas/incidents.py backend/src/incident_intelligence/api/routes/health.py backend/src/incident_intelligence/main.py backend/tests/integration/services/test_incident_evidence.py backend/tests/api/test_incident_evidence.py backend/tests/api/test_health.py
git commit -m "feat: 提供 Incident 监控取证 API"
```

---

### Task 11: 第三版 Incident 取证前端

**Files:**
- Create: `frontend/src/api/incidentEvidence.js`
- Create: `frontend/src/api/incidentEvidence.test.js`
- Create: `frontend/src/composables/useIncidentEvidence.js`
- Create: `frontend/src/composables/useIncidentEvidence.test.js`
- Create: `frontend/src/presentation/evidenceView.js`
- Create: `frontend/src/presentation/evidenceView.test.js`
- Create: `frontend/src/components/IncidentEvidence.vue`
- Create: `frontend/src/components/IncidentEvidence.test.js`
- Create: `frontend/src/components/EvidenceFinding.vue`
- Modify: `frontend/src/components/IncidentDetail.vue`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/components/IncidentDetail.test.js`

**Interfaces:**
- Consumes: Task 10 的运行列表、运行详情和人工取证 API。
- Produces: `IncidentEvidence` 页面、`useIncidentEvidence(incidentId)`、中文安全投影。

- [ ] **Step 1: 写 API 与投影失败测试**

```javascript
it("重新取证发送幂等键且不提交查询文本", async () => {
  await requestEvidenceRun("inc_1", "key-1");
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/inc_1/evidence-runs"), expect.objectContaining({
    method: "POST", headers: expect.objectContaining({ "Idempotency-Key": "key-1" }),
  }));
  expect(fetch.mock.calls[0][1].body).toBeUndefined();
});

it("把部分成功和无数据区分成中文状态", () => {
  expect(toEvidenceRunView({ state: "PARTIAL" }).stateLabel).toBe("部分证据缺失");
  expect(toEvidenceItemView({ state: "NO_DATA" }).stateLabel).toBe("未发现数据");
});
```

- [ ] **Step 2: 运行前端定向测试并确认失败**

Run: `cd frontend && npm test -- src/api/incidentEvidence.test.js src/presentation/evidenceView.test.js`

Expected: FAIL。

- [ ] **Step 3: 实现 API、投影和 composable**

`useIncidentEvidence` 独立管理运行列表、选中运行、详情、重新取证和 AbortController；切换 Incident 时取消旧请求。409 active-run 冲突读取响应中的 run ID 并刷新该运行，不虚构成功。

- [ ] **Step 4: 写组件失败测试**

```javascript
it("默认展开第一条异常发现并收起正常证据", () => {
  const wrapper = mount(IncidentEvidence, { props: { state: readyEvidenceState() } });
  expect(wrapper.get('[data-testid="finding-abnormal"]').attributes("open")).toBeDefined();
  expect(wrapper.get('[data-testid="finding-normal"]').attributes("open")).toBeUndefined();
});

it("部分成功同时显示成功证据和缺失来源", () => {
  const wrapper = mount(IncidentEvidence, { props: { state: partialEvidenceState() } });
  expect(wrapper.text()).toContain("部分证据缺失");
  expect(wrapper.text()).toContain("Prometheus");
  expect(wrapper.text()).toContain("SkyWalking 查询失败");
});
```

- [ ] **Step 5: 实现第三版页面结构**

在 `IncidentDetail` 内增加“当前情况、关联告警、监控取证、处置与飞书”切换；监控取证采用：主区关键发现列表、向下展开证据、右侧来源状态/时间窗口/规则包、底部按数据源查看。低于 1,180px 右栏并入主区。正文不小于 12px，关键结果不只依赖红绿颜色。

- [ ] **Step 6: 运行组件、全量前端测试和构建**

Run: `cd frontend && npm test`

Expected: 全部 PASS。

Run: `cd frontend && npm run build`

Expected: 生产构建成功。

- [ ] **Step 7: 提交前端**

```bash
git add frontend/src/api/incidentEvidence.js frontend/src/api/incidentEvidence.test.js frontend/src/composables/useIncidentEvidence.js frontend/src/composables/useIncidentEvidence.test.js frontend/src/presentation/evidenceView.js frontend/src/presentation/evidenceView.test.js frontend/src/components/IncidentEvidence.vue frontend/src/components/IncidentEvidence.test.js frontend/src/components/EvidenceFinding.vue frontend/src/components/IncidentDetail.vue frontend/src/components/IncidentDetail.test.js frontend/src/styles.css
git commit -m "feat: 展示 Incident 监控取证"
```

---

### Task 12: 测试环境只读联调、统一验证与状态文档

**Files:**
- Create: `backend/scripts/inspect_monitoring_contracts.py`
- Test: `backend/tests/unit/scripts/test_inspect_monitoring_contracts.py`
- Modify: `backend/src/incident_intelligence/domain/evidence_packs.py`
- Modify: `docs/current-state.md`
- Modify: `docs/architecture.md`
- Modify: `docs/product.md`
- Modify: `specs/active/monitoring-evidence-collection.md`
- Create: `docs/verification/2026-09-02-monitoring-evidence-collection.md`

**Interfaces:**
- Consumes: 用户运行环境提供的三个数据源地址和凭据引用。
- Produces: 当前测试环境可执行的 v1 查询模板、真实只读联调证据和最终文档。

- [ ] **Step 1: 写检查脚本的安全输出失败测试**

```python
def test_inspector_reports_capabilities_without_secret_query_or_raw_log(capsys):
    exit_code = main(fake_clients=healthy_clients(), environ={"II_PROMETHEUS_TOKEN": "secret"})
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "secret" not in output
    assert "PROMETHEUS available" in output
    assert "ELASTICSEARCH available" in output
    assert "SKYWALKING available" in output
```

- [ ] **Step 2: 实现只读契约检查脚本**

脚本只输出版本、可用标签/字段名称、安全数量和兼容性，不输出 URL、凭据、PromQL、DSL、日志内容、Trace ID 或原始响应。默认不运行真实查询，必须显式传 `--execute-read-only` 才执行取证包试查询。

- [ ] **Step 3: 运行脚本单元测试**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/scripts/test_inspect_monitoring_contracts.py -v`

Expected: PASS。

- [ ] **Step 4: 在测试环境检查真实契约并固化 v1 模板**

Run: `cd backend && .venv/bin/python scripts/inspect_monitoring_contracts.py --environment testing --execute-read-only`

Expected: 三类来源均显示 `available`，报告服务标签/字段兼容；若某来源未配置，输出 `credential_missing` 并保持其他来源继续检查。根据安全结果更新 `evidence_packs.py`，只加入当前环境实际存在且已用只读调用验证的模板。

- [ ] **Step 5: 运行统一后端验证**

Run: `./scripts/verify-backend.sh`

Expected: Ruff、格式检查、mypy、全部 pytest 通过，覆盖率不低于 90%。

- [ ] **Step 6: 运行全量前端测试与生产构建**

Run: `cd frontend && npm test && npm run build`

Expected: 全部测试和构建通过。

- [ ] **Step 7: 完成真实浏览器主流程**

在本地页面验证：新 Incident 自动出现取证运行；运行中状态可见；完成后第一条异常发现默认展开；历史运行可切换；人工重新取证不会覆盖历史；模拟 SkyWalking 失败时页面保留 Prometheus/ELK 证据并显示“部分证据缺失”。把 Incident 编号、运行状态、测试数量和截图路径记录到验证文档，不记录 Secret、原始日志或 Trace ID。

- [ ] **Step 8: 更新产品、架构、当前状态和规格**

只把真实通过验证的能力写为“当前可用”；未完成真实联调的来源明确写入已知缺口。将活跃规格状态改为“已确认，实现与自动化验证完成，等待用户页面验收”，用户验收后再移动到 `specs/completed/`。

- [ ] **Step 9: 提交联调与验证文档**

```bash
git add backend/scripts/inspect_monitoring_contracts.py backend/tests/unit/scripts/test_inspect_monitoring_contracts.py backend/src/incident_intelligence/domain/evidence_packs.py docs/current-state.md docs/architecture.md docs/product.md specs/active/monitoring-evidence-collection.md docs/verification/2026-09-02-monitoring-evidence-collection.md
git commit -m "test: 验证 Incident 监控取证闭环"
```

---

## 最终完成检查

- [ ] `git status --short` 只包含用户原有未提交改动，不包含本计划遗漏文件。
- [ ] `./scripts/verify-backend.sh` 成功，后端覆盖率不低于 90%。
- [ ] `cd frontend && npm test && npm run build` 成功。
- [ ] 数据库从 base 升级到 head、降级到 base、再次升级到 head 全部成功。
- [ ] Incident 创建、自动取证、部分失败、人工重取、历史切换和证据展开主流程通过。
- [ ] API、日志、健康响应、验证文档中没有 Secret、完整监控响应、原始日志或 Trace ID。
- [ ] 取证失败不阻断 Alert 接收、Incident 确认/解决和飞书协同。
- [ ] 文档只宣称经过测试和真实验证的能力。
