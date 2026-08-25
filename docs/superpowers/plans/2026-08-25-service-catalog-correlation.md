# 服务目录与可解释事故关联实施计划

> **执行要求：** 使用 `superpowers:executing-plans` 按任务顺序实施，步骤使用复选框跟踪。本项目明确禁止子 Agent、功能分支和 worktree，所有变更由当前会话直接提交到 `main`。

**目标：** 在已验收的多源 Alert 投影之后，实现版本化服务目录、持久关联任务和规则优先的可解释 Incident 创建与关联。

**架构：** Alert 真正改变投影时，在同一 MySQL 事务内追加唯一关联任务；核心控制器通过租约领取任务，由纯领域规则和事务服务创建或关联 Incident，并保存不可变决策。服务目录、任务、决策和关系使用独立模型与仓储，关联失败不回滚已经提交的 SignalEvent 或 Alert。

**技术栈：** Python 3.13/3.14、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、PyMySQL、MySQL 8.4、Pytest、Ruff、Mypy。

**规格：** `docs/superpowers/specs/2026-08-25-service-catalog-correlation-design.md`

## 全局约束

- 直接在 `main` 分支顺序开发，不创建分支、worktree 或子 Agent。
- 先写失败测试并确认失败原因，再写最小实现；每个任务独立验证和提交。
- 不修改已经应用的 `0001_mysql_initial`，新增 `0002_service_catalog_correlation`。
- 只允许 MySQL 8.4、InnoDB、utf8mb4、utf8mb4_bin、READ COMMITTED 和 UTC `DATETIME(6)`。
- 外部 Alert 只能在关联阶段创建 Incident，不得创建 DiagnosisRun。
- 只有 ACTIVE、critical/high、production、目录 ACTIVE 的 Alert 才能创建或加入 Incident。
- 同服务 15 分钟唯一候选可以自动关联；多个候选和一跳同症状不得自动合并。
- 关联规则固定为 `correlation.v1`，中文解释只来自固定模板。
- Secret、原始来源 URI、生成器 URL、查询、完整外部负载、异常正文和实验身份不得进入任务、决策、审计、响应或测试数据。
- 所有新增集合、字符串、分页、领取批次、候选和重试次数必须有明确上界。
- 目录或关联不可用不得阻断健康检查、告警接入和人工事故处置。

---

### 任务 1：实现标准症状、门槛和纯关联决策

**文件：**

- 创建：`backend/src/incident_intelligence/domain/catalog.py`
- 创建：`backend/src/incident_intelligence/domain/correlation.py`
- 修改：`backend/src/incident_intelligence/domain/enums.py`
- 修改：`backend/src/incident_intelligence/adapters/alertmanager.py`
- 创建：`backend/tests/unit/domain/test_catalog.py`
- 创建：`backend/tests/unit/domain/test_correlation.py`
- 修改：`backend/tests/unit/adapters/test_alertmanager.py`

**接口：**

- 产生：`CatalogState`、`DependencyState`、`CorrelationJobState`、`CorrelationOutcome`、`LinkRelation`。
- 产生：`ServiceCatalogEntry`、`ServiceDependency` 两个不可变 Pydantic 领域模型。
- 产生：`normalize_symptom(value: str | None) -> Symptom | None`。
- 产生：`CorrelationContext`、`CorrelationDecisionDraft` 和 `decide_correlation(context)`。

- [ ] **步骤 1：编写标准症状和目录模型失败测试**

```python
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("high-error-rate", "error_rate"),
        ("latency", "latency"),
        ("cpu", "cpu_saturation"),
        ("oom", "memory_pressure"),
        ("down", "availability"),
        ("private-new-symptom", None),
        (None, None),
    ],
)
def test_symptom_normalization_uses_only_fixed_aliases(raw, expected):
    assert normalize_symptom(raw) == expected


def test_catalog_entry_rejects_invalid_id_and_unbounded_owner():
    with pytest.raises(ValidationError):
        ServiceCatalogEntry(
            id="service-1",
            service="payment-api",
            environment="production",
            owner_team="x" * 129,
            state="ACTIVE",
            created_at=NOW,
            updated_at=NOW,
        )
```

- [ ] **步骤 2：运行领域测试并确认模块不存在**

运行：

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest -q tests/unit/domain/test_catalog.py -x
```

预期：收集阶段因 `incident_intelligence.domain.catalog` 不存在而失败。

- [ ] **步骤 3：实现固定枚举、领域模型和症状别名**

在 `domain/enums.py` 增加字符串枚举；在 `domain/catalog.py` 使用 `ConfigDict(frozen=True, extra="forbid")` 和现有 `Environment`、`ServiceName`、`UtcAwareDatetime`：

```python
class CatalogState(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class CorrelationJobState(StrEnum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ServiceCatalogEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(pattern=r"^svc_[0-9a-f]{32}$")
    service: ServiceName
    environment: Environment
    owner_team: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    state: CatalogState
    created_at: UtcAwareDatetime
    updated_at: UtcAwareDatetime
    version: int = Field(default=1, ge=1)
```

`normalize_symptom` 只返回 `error_rate | latency | cpu_saturation | memory_pressure | availability | None`。把 `symptom` 加入 Alertmanager `FACT_LABELS`，但不保存未知值的派生结果。

- [ ] **步骤 4：编写门槛与决策矩阵失败测试**

测试使用字面量构造 `CorrelationContext`，分别断言：

```python
def test_unique_exact_service_candidate_is_the_only_auto_link_case():
    decision = decide_correlation(
        eligible_context(exact_candidate_ids=("inc_" + "1" * 32,))
    )
    assert decision.outcome == "LINKED_EXACT_SERVICE"
    assert decision.action == "LINK"
    assert decision.selected_incident_id == "inc_" + "1" * 32
    assert decision.reason_codes == ("one_exact_service_candidate",)
    assert decision.explanation == "窗口内只有一个同服务事故，已自动关联。"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"alert_state": "RESOLVED"}, "alert_not_active"),
        ({"severity": "medium"}, "severity_below_threshold"),
        ({"environment": "staging"}, "non_production_environment"),
        ({"catalog_state": None}, "service_not_registered"),
        ({"catalog_state": "INACTIVE"}, "service_inactive"),
    ],
)
def test_ineligible_alerts_are_rejected_with_one_fixed_reason(overrides, reason):
    decision = decide_correlation(eligible_context(**overrides))
    assert decision.outcome == "REJECTED_INELIGIBLE"
    assert decision.action == "NONE"
    assert decision.reason_codes == (reason,)
```

还要分别验证：旧版本 `SUPERSEDED`、已有关系 `LINKED_EXISTING`、已有关系且恢复 `RECORDED_RESOLUTION`、零候选 `CREATED_NO_MATCH`、多候选 `CREATED_AMBIGUOUS`、一跳同症状 `CREATED_DEPENDENCY_CANDIDATE`、候选超过 20 条使用 `candidate_limit_reached`。

- [ ] **步骤 5：实现纯 `decide_correlation`**

`CorrelationContext` 必须携带 `alert_version`、`current_alert_version`、状态、严重度、环境、目录状态、已有关系、精确候选和一跳候选；`CorrelationDecisionDraft` 固定为：

```python
class CorrelationDecisionDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    outcome: CorrelationOutcome
    action: Literal["CREATE", "LINK", "NONE"]
    selected_incident_id: str | None = None
    candidate_incident_ids: tuple[str, ...] = Field(default=(), max_length=20)
    reason_codes: tuple[CorrelationReasonCode, ...] = Field(min_length=1, max_length=10)
    explanation: Annotated[str, StringConstraints(min_length=1, max_length=500)]
```

函数按设计第 6 节的顺序提前返回，不能读取数据库、当前时间或 ID 生成器。中文解释由 `EXPLANATIONS` 固定字典取得，动态内容只允许候选数量和 Incident ID。

- [ ] **步骤 6：运行纯领域与 Alertmanager 测试**

```bash
cd backend
PYTHONPATH=. .venv/bin/pytest -q \
  tests/unit/domain/test_catalog.py \
  tests/unit/domain/test_correlation.py \
  tests/unit/adapters/test_alertmanager.py
```

预期：标准症状、全部决策分支、未知值退化和 Alertmanager symptom 白名单测试通过。

- [ ] **步骤 7：提交纯领域规则**

```bash
git add backend/src/incident_intelligence/domain \
  backend/src/incident_intelligence/adapters/alertmanager.py \
  backend/tests/unit/domain \
  backend/tests/unit/adapters/test_alertmanager.py
git commit -m "feat: 定义可解释事故关联规则"
```

---

### 任务 2：增加 MySQL 迁移和 ORM 模型

**文件：**

- 创建：`backend/migrations/versions/0002_service_catalog_correlation.py`
- 修改：`backend/src/incident_intelligence/persistence/models.py`
- 修改：`backend/src/incident_intelligence/ids.py`
- 修改：`backend/tests/integration/persistence/test_initial_migration.py`
- 创建：`backend/tests/integration/persistence/test_correlation_migration.py`
- 修改：`backend/tests/integration/persistence/test_constraints.py`

**接口：**

- 产生：`ServiceCatalogEntryRow`、`ServiceCatalogStateRow`、`ServiceDependencyRow`、`CorrelationJobRow`、`CorrelationDecisionRow`、`IncidentAlertLinkRow`。
- 扩展：`IdPrefix` 支持 `svc`、`dep`、`cjob`、`cdec`。

- [ ] **步骤 1：编写迁移结构与回填失败测试**

测试先升级到 `0001_mysql_initial`，插入一套合法人工 SignalEvent、Alert 和 Incident，再升级到 head：

```python
EXPECTED_CORRELATION_TABLES = {
    "service_catalog_entries",
    "service_catalog_state",
    "service_dependencies",
    "correlation_jobs",
    "correlation_decisions",
    "incident_alert_links",
}


def test_upgrade_adds_correlation_tables_and_backfills_primary_links(
    alembic_config, engine, session_factory
):
    command.upgrade(config, "0001_mysql_initial")
    seed_manual_incident(engine)
    command.upgrade(config, "head")
    assert EXPECTED_CORRELATION_TABLES <= set(inspect(engine).get_table_names())
    with Session(engine) as session:
        link = session.scalar(select(IncidentAlertLinkRow))
        assert link.relation == "PRIMARY"
        assert link.decision_id is None
```

另写测试从 head 降级到 `0001_mysql_initial`，断言六张新增表消失而七张基线表和人工数据仍存在；`command.check` 必须通过。

- [ ] **步骤 2：运行迁移测试并确认 revision 不存在**

```bash
cd backend
II_TEST_DATABASE_URL="$II_MYSQL_BOOTSTRAP_URL" PYTHONPATH=. \
  .venv/bin/pytest -q tests/integration/persistence/test_correlation_migration.py -x
```

预期：因 `0002_service_catalog_correlation` 和 ORM 行模型不存在而失败。

- [ ] **步骤 3：实现 ORM 和 `0002` 迁移**

六张表全部声明 InnoDB、utf8mb4、utf8mb4_bin。迁移顺序固定为：目录项、目录图状态、依赖、任务、决策、关系；写入单例图状态：

```python
op.bulk_insert(
    sa.table(
        "service_catalog_state",
        sa.column("id", sa.String(16)),
        sa.column("graph_version", sa.Integer()),
        sa.column("updated_at", utc_datetime()),
    ),
    [{"id": "global", "graph_version": 1, "updated_at": datetime.now(UTC)}],
)
```

`correlation_jobs` 唯一约束为 `(alert_id, alert_version)`；`incident_alert_links` 对 `alert_id` 单独唯一；`decision_id` 可空。JSON 字段使用 MySQL JSON，所有状态和次数使用 CHECK 约束。回填 SQL 从 `incidents` 复制 `primary_alert_id`、`created_at` 到 PRIMARY 关系。

- [ ] **步骤 4：增加真实数据库约束失败测试**

覆盖以下真实 MySQL 行为：

测试先插入一个完整的 PRIMARY 关系，再以相同 `alert_id`、不同 `incident_id` 和完整必填字段插入 RELATED 关系；第二次 `commit()` 必须抛出 `IntegrityError`，回滚后数据库只保留第一条关系。

同时验证重复目录身份、重复依赖边、自依赖、非法状态、attempts > 5、重复 `(alert_id, alert_version)` 和候选 JSON/时间类型。

- [ ] **步骤 5：运行迁移、约束和 ORM 一致性测试**

```bash
cd backend
II_TEST_DATABASE_URL="$II_MYSQL_BOOTSTRAP_URL" PYTHONPATH=. .venv/bin/pytest -q \
  tests/integration/persistence/test_initial_migration.py \
  tests/integration/persistence/test_correlation_migration.py \
  tests/integration/persistence/test_constraints.py
```

预期：升级、回填、降级、约束和 `alembic check` 全部通过。

- [ ] **步骤 6：提交迁移和 ORM**

```bash
git add backend/migrations/versions/0002_service_catalog_correlation.py \
  backend/src/incident_intelligence/persistence/models.py \
  backend/src/incident_intelligence/ids.py \
  backend/tests/integration/persistence
git commit -m "feat: 持久化服务目录与关联任务"
```

---

### 任务 3：实现版本化服务目录业务服务

**文件：**

- 创建：`backend/src/incident_intelligence/persistence/catalog_repository.py`
- 修改：`backend/src/incident_intelligence/persistence/unit_of_work.py`
- 创建：`backend/src/incident_intelligence/services/catalog.py`
- 创建：`backend/tests/integration/services/test_catalog_service.py`

**接口：**

- 产生：`CreateServiceCommand`、`UpdateServiceCommand`、`CreateDependencyCommand`、`UpdateDependencyCommand`。
- 产生：`CatalogConflict`、`CatalogVersionConflict`、`InvalidDependency`、`CatalogResourceNotFound`。
- 产生：`ServiceCatalogService.create_service/update_service/create_dependency/update_dependency/list_services/list_dependencies`。

- [ ] **步骤 1：编写服务唯一、版本和停用失败测试**

```python
def test_service_identity_is_unique_and_update_requires_expected_version(service):
    created = service.create_service(
        CreateServiceCommand(service="payment-api", environment="production", owner_team="payments"),
        actor="manual-api-client",
        request_id="req-1",
    )
    with pytest.raises(CatalogConflict):
        service.create_service(
            CreateServiceCommand(
                service="payment-api",
                environment="production",
                owner_team="payments",
            ),
            actor="manual-api-client",
            request_id="req-2",
        )
    with pytest.raises(CatalogVersionConflict):
        service.update_service(
            created.id,
            UpdateServiceCommand(expected_version=99, owner_team="platform"),
            actor="manual-api-client",
            request_id="req-3",
        )
```

断言有效更新 version 加一、停用不删除行、审计只包含 `reason_code`、`previous_state`、`new_state`、`previous_version`、`new_version`。

- [ ] **步骤 2：运行服务测试并确认服务不存在**

```bash
cd backend
II_TEST_DATABASE_URL="$II_MYSQL_BOOTSTRAP_URL" PYTHONPATH=. \
  .venv/bin/pytest -q tests/integration/services/test_catalog_service.py -x
```

预期：因 `services.catalog` 不存在而失败。

- [ ] **步骤 3：实现目录仓储和服务事务**

`ServiceCatalogRepository` 提供精确方法：

```python
def find_service(self, service_id: str, *, for_update: bool = False) -> ServiceCatalogEntryRow | None:
    raise NotImplementedError

def find_service_identity(self, service: str, environment: str) -> ServiceCatalogEntryRow | None:
    raise NotImplementedError

def lock_graph_state(self) -> ServiceCatalogStateRow:
    raise NotImplementedError

def active_edges(self) -> tuple[ServiceDependencyRow, ...]:
    raise NotImplementedError

def add_service(self, row: ServiceCatalogEntryRow) -> None:
    raise NotImplementedError

def add_dependency(self, row: ServiceDependencyRow) -> None:
    raise NotImplementedError

def list_services(
    self,
    *,
    state: str | None,
    environment: str | None,
    limit: int,
    offset: int,
) -> tuple[ServiceCatalogEntryRow, ...]:
    raise NotImplementedError
```

`SqlAlchemyUnitOfWork.__enter__` 同时创建 `records`、`catalog` 和后续 `correlation` 仓储。服务层在一个 UoW 内完成查重、乐观锁、写入和审计；捕获唯一约束时必须退出失败事务后映射固定冲突。

- [ ] **步骤 4：编写依赖图失败测试**

测试创建 A、B、C 三个同环境服务，覆盖：

```python
def test_dependency_cycle_is_rejected_atomically(service):
    service.create_dependency(
        CreateDependencyCommand(upstream_service_id=A, downstream_service_id=B),
        actor="manual-api-client",
        request_id="req-ab",
    )
    service.create_dependency(
        CreateDependencyCommand(upstream_service_id=B, downstream_service_id=C),
        actor="manual-api-client",
        request_id="req-bc",
    )
    with pytest.raises(InvalidDependency) as error:
        service.create_dependency(
            CreateDependencyCommand(upstream_service_id=C, downstream_service_id=A),
            actor="manual-api-client",
            request_id="req-ca",
        )
    assert error.value.reason_code == "dependency_cycle"
    assert list_active_pairs() == [(A, B), (B, C)]
```

还要验证自依赖、跨环境、重复边、停用再启用、错误 expected_version，以及两个并发事务尝试组成环时最多一个成功。每次依赖变化断言 `service_catalog_state.graph_version + 1`。

- [ ] **步骤 5：实现串行化图更新和 DFS 环检测**

依赖写入必须先 `SELECT ... FOR UPDATE` 锁定 `service_catalog_state(global)`，再加载 ACTIVE 边，加入或移除目标边后运行颜色标记 DFS：

```python
def _has_cycle(edges: set[tuple[str, str]]) -> bool:
    adjacency = _adjacency(edges)
    visiting: set[str] = set()
    visited: set[str] = set()
    return any(_visit(node, adjacency, visiting, visited) for node in adjacency)
```

校验通过后才写依赖并递增图版本；异常时整个事务回滚。

- [ ] **步骤 6：运行服务目录完整测试**

```bash
cd backend
II_TEST_DATABASE_URL="$II_MYSQL_BOOTSTRAP_URL" PYTHONPATH=. \
  .venv/bin/pytest -q tests/integration/services/test_catalog_service.py
```

预期：服务、版本、依赖、并发环和有界审计全部通过。

- [ ] **步骤 7：提交目录业务服务**

```bash
git add backend/src/incident_intelligence/persistence/catalog_repository.py \
  backend/src/incident_intelligence/persistence/unit_of_work.py \
  backend/src/incident_intelligence/services/catalog.py \
  backend/tests/integration/services/test_catalog_service.py
git commit -m "feat: 管理版本化服务目录"
```

---

### 任务 4：暴露服务目录管理 API

**文件：**

- 创建：`backend/src/incident_intelligence/api/schemas/catalog.py`
- 创建：`backend/src/incident_intelligence/api/routes/catalog.py`
- 修改：`backend/src/incident_intelligence/api/dependencies.py`
- 修改：`backend/src/incident_intelligence/api/errors.py`
- 修改：`backend/src/incident_intelligence/api/router.py`
- 修改：`backend/src/incident_intelligence/main.py`
- 创建：`backend/tests/api/test_catalog.py`

**接口：**

- 产生：设计第 8 节的七个 `/api/v1/catalog` 接口。
- 产生：固定 404、409、422、503 中文错误契约和有界分页响应。

- [ ] **步骤 1：编写认证、创建和版本冲突失败 API 测试**

```python
def test_only_manual_token_can_create_catalog_service(client, tokens):
    payload = {"service": "payment-api", "environment": "production", "owner_team": "payments"}
    for token_name in (None, "alertmanager", "cloudevents"):
        response = client.post("/api/v1/catalog/services", headers=auth(tokens, token_name), json=payload)
        assert response.status_code == 401
    created = client.post("/api/v1/catalog/services", headers=auth(tokens, "manual"), json=payload)
    assert created.status_code == 201
    assert created.json()["version"] == 1


def test_stale_expected_version_returns_safe_conflict(
    client, manual_headers, seeded_catalog_service
):
    service_id = seeded_catalog_service.id
    response = client.patch(
        f"/api/v1/catalog/services/{service_id}",
        headers=manual_headers,
        json={"expected_version": 99, "state": "INACTIVE"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "catalog_version_conflict"
```

- [ ] **步骤 2：运行 API 测试并确认路由 404**

```bash
cd backend
II_TEST_DATABASE_URL="$II_MYSQL_BOOTSTRAP_URL" PYTHONPATH=. \
  .venv/bin/pytest -q tests/api/test_catalog.py -x
```

预期：合法创建返回 404。

- [ ] **步骤 3：实现严格 Schema、依赖注入和目录路由**

请求模型全部 `extra="forbid"`；列表参数固定 `limit=50, ge=1, le=100`、`offset ge=0, le=10000`。`get_catalog_service` 从 `app.state.catalog_service` 返回业务服务。路由异常映射：

```python
except CatalogConflict as error:
    raise ApiError(409, "catalog_conflict", "同环境服务或依赖关系已存在") from error
except CatalogVersionConflict as error:
    raise ApiError(409, "catalog_version_conflict", "资源版本已变化，请刷新后重试") from error
except InvalidDependency as error:
    raise ApiError(422, "invalid_dependency", "服务依赖关系不符合约束") from error
```

响应只返回目录白名单字段和分页元数据，不返回审计或内部图状态。

- [ ] **步骤 4：补齐分页、依赖和安全边界测试**

覆盖 101 上限、非法筛选、未知字段、禁止实验身份、重复服务、依赖环、停用和读取不存在。数据库故障返回固定 503；`caplog`、错误响应和审计不得包含 owner_team 原始请求以外的正文、Token 或禁止值。

- [ ] **步骤 5：运行目录 API 与旧接口回归**

```bash
cd backend
II_TEST_DATABASE_URL="$II_MYSQL_BOOTSTRAP_URL" PYTHONPATH=. .venv/bin/pytest -q \
  tests/api/test_catalog.py \
  tests/api/test_manual_reports.py \
  tests/api/test_alertmanager_intake.py \
  tests/api/test_cloudevents_intake.py \
  tests/api/test_resources.py
```

预期：管理 Token 隔离和全部现有 API 契约通过。

- [ ] **步骤 6：提交目录 API**

```bash
git add backend/src/incident_intelligence/api \
  backend/src/incident_intelligence/main.py \
  backend/tests/api/test_catalog.py
git commit -m "feat: 暴露服务目录管理接口"
```

---

### 任务 5：同事务生成关联任务并实现租约

**文件：**

- 创建：`backend/src/incident_intelligence/persistence/correlation_repository.py`
- 修改：`backend/src/incident_intelligence/persistence/unit_of_work.py`
- 修改：`backend/src/incident_intelligence/services/signal_intake.py`
- 创建：`backend/src/incident_intelligence/services/correlation_jobs.py`
- 修改：`backend/src/incident_intelligence/settings.py`
- 修改：`backend/tests/conftest.py`
- 修改：`backend/tests/integration/services/test_signal_intake_service.py`
- 创建：`backend/tests/integration/services/test_correlation_jobs.py`

**接口：**

- 产生：`CorrelationJobLease`。
- 产生：`CorrelationJobService.claim_batch/complete/fail/retry_failed`。
- 修改：SignalIntakeService 对 opened、updated、resolved、reopened 追加唯一任务。

- [ ] **步骤 1：编写 Alert 与任务同事务失败测试**

```python
@pytest.mark.parametrize("outcome", ["opened", "updated", "resolved", "reopened"])
def test_projection_change_enqueues_exactly_one_matching_job(
    service, session_factory, outcome
):
    result = service.submit_batch((command_for(outcome),), "alertmanager-adapter", "req-1")
    with session_factory() as session:
        job = session.scalar(select(CorrelationJobRow))
        alert = session.get(AlertRow, result.items[0].alert_id)
        assert (job.alert_id, job.alert_version) == (alert.id, alert.version)


@pytest.mark.parametrize("outcome", ["stale", "orphan_resolved"])
def test_non_projection_results_and_replay_do_not_enqueue(
    service, session_factory, outcome
):
    assert count_jobs() == 0
```

在现有整批回滚测试中增加 `CorrelationJobRow == 0`，证明任务写入失败会与本次 Alert 变化共同回滚。

- [ ] **步骤 2：运行共享接入测试并确认没有任务**

```bash
cd backend
II_TEST_DATABASE_URL="$II_MYSQL_BOOTSTRAP_URL" PYTHONPATH=. \
  .venv/bin/pytest -q tests/integration/services/test_signal_intake_service.py -x
```

预期：新增任务断言失败，实际数量为 0。

- [ ] **步骤 3：实现任务仓储和接入服务排队**

`CorrelationRepository.enqueue(alert_id, alert_version, now)` 创建 `cjob_` ID、PENDING、attempts=0、available_at=now。`SignalIntakeService` 只在 `decision.changes_projection` 且存在 Alert 时调用；重放路径在此之前返回，不会追加任务。

唯一约束竞争沿用共享接入服务的“退出失败事务后全新事务单次重试”，任务不单独吞掉 IntegrityError。

- [ ] **步骤 4：编写租约、接管和重试失败测试**

```python
def test_claim_uses_lease_and_expired_job_can_be_reclaimed(job_service):
    first = job_service.claim_batch("runner-a", now=NOW, limit=1, lease_seconds=30)
    assert first[0].attempts == 1
    assert job_service.claim_batch("runner-b", now=NOW, limit=1, lease_seconds=30) == ()
    reclaimed = job_service.claim_batch("runner-b", now=NOW + timedelta(seconds=31), limit=1, lease_seconds=30)
    assert reclaimed[0].id == first[0].id
    assert reclaimed[0].attempts == 2
```

另测两线程并发 `claim_batch` 不返回同一 ID、非 lease_owner 不能 complete、失败使用固定退避、第五次失败进入 FAILED、人工 retry 只接受 FAILED 且清空 lease/error。

- [ ] **步骤 5：实现 `SELECT FOR UPDATE SKIP LOCKED` 任务服务**

领取查询只选择 available 的 PENDING 或租约过期 LEASED，按 `available_at, created_at, id` 排序，limit 1–50。Settings 增加：

```python
correlation_runner_enabled: bool = True
correlation_poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=60)
correlation_lease_seconds: int = Field(default=30, ge=5, le=300)
correlation_batch_size: int = Field(default=10, ge=1, le=50)
```

测试 `settings_factory` 默认设置 `correlation_runner_enabled=False`，避免 API 测试后台消费任务。

- [ ] **步骤 6：运行接入与任务并发测试**

```bash
cd backend
II_TEST_DATABASE_URL="$II_MYSQL_BOOTSTRAP_URL" PYTHONPATH=. .venv/bin/pytest -q \
  tests/integration/services/test_signal_intake_service.py \
  tests/integration/services/test_correlation_jobs.py
```

预期：任务原子性、唯一性、SKIP LOCKED、租约接管和五次上限全部通过。

- [ ] **步骤 7：提交关联任务能力**

```bash
git add backend/src/incident_intelligence/persistence \
  backend/src/incident_intelligence/services/signal_intake.py \
  backend/src/incident_intelligence/services/correlation_jobs.py \
  backend/src/incident_intelligence/settings.py \
  backend/tests/conftest.py \
  backend/tests/integration/services
git commit -m "feat: 排队并租用事故关联任务"
```

---

### 任务 6：实现事务化事故关联服务

**文件：**

- 修改：`backend/src/incident_intelligence/persistence/correlation_repository.py`
- 创建：`backend/src/incident_intelligence/services/correlation.py`
- 修改：`backend/src/incident_intelligence/persistence/repositories.py`
- 创建：`backend/tests/integration/services/test_correlation_service.py`

**接口：**

- 产生：`CorrelationService.process(lease: CorrelationJobLease) -> CorrelationDecisionResult`。
- 消费：任务 1 的 `decide_correlation`，任务 3 的目录仓储，任务 5 的租约。

- [ ] **步骤 1：编写零候选创建与唯一候选关联失败测试**

```python
def test_eligible_alert_without_candidate_creates_incident_link_and_decision(context):
    result = context.process_alert(alert(service="payment-api"))
    assert result.outcome == "CREATED_NO_MATCH"
    with context.session() as session:
        assert count(session, IncidentRow) == 1
        assert count(session, IncidentAlertLinkRow) == 1
        assert count(session, CorrelationDecisionRow) == 1
        assert count(session, DiagnosisRunRow) == 0


def test_only_one_same_service_candidate_is_auto_linked(context):
    existing_incident = context.seed_open_incident(service="payment-api", detected_at=NOW)
    result = context.process_alert(alert(service="payment-api", observed_at=NOW + timedelta(minutes=15)))
    assert result.outcome == "LINKED_EXACT_SERVICE"
    assert result.incident_id == existing_incident.id
    assert count_incidents() == 1
```

- [ ] **步骤 2：运行关联服务测试并确认服务不存在**

```bash
cd backend
II_TEST_DATABASE_URL="$II_MYSQL_BOOTSTRAP_URL" PYTHONPATH=. \
  .venv/bin/pytest -q tests/integration/services/test_correlation_service.py -x
```

预期：因 `services.correlation` 不存在而失败。

- [ ] **步骤 3：实现候选查询、范围锁和创建/关联事务**

`CorrelationRepository` 增加：

```python
def lock_job(self, job_id: str, lease_owner: str) -> CorrelationJobRow | None:
    raise NotImplementedError

def lock_alert(self, alert_id: str) -> AlertRow | None:
    raise NotImplementedError

def find_alert_link(self, alert_id: str) -> IncidentAlertLinkRow | None:
    raise NotImplementedError

def exact_incident_candidates(
    self,
    service: str,
    environment: str,
    observed_at: datetime,
    limit: int = 21,
) -> tuple[IncidentRow, ...]:
    raise NotImplementedError

def dependency_candidates(
    self,
    adjacent_service_ids: tuple[str, ...],
    symptom: str,
    environment: str,
    observed_at: datetime,
    limit: int = 21,
) -> tuple[IncidentRow, ...]:
    raise NotImplementedError

def add_incident_link_decision(self, incident, link, decision) -> None:
    raise NotImplementedError
```

处理事务先验证 job owner/state 和 Alert 版本，再读取当前 SignalEvent 的 symptom。符合门槛后锁服务目录项，查询候选并调用纯决策。CREATE 顺序为 Incident、CorrelationDecision、IncidentAlertLink；LINK 顺序为 CorrelationDecision、IncidentAlertLink、可选严重度提升。最后把任务设为 SUCCEEDED。任一步异常回滚全部业务写入。

- [ ] **步骤 4：补齐规则矩阵和安全测试**

逐项验证：

- 多个同服务候选创建独立事故并保存最多 20 个候选；
- 一跳双向邻接且同症状创建独立事故和候选；
- 未知症状、跨环境、窗口超过 900 秒和终态事故不是候选；
- 不符合四项门槛只保存 REJECTED 决策，不创建 Incident；
- 已有关系的 ACTIVE 更新使用 LINKED_EXISTING；
- 已有关系的 RESOLVED 使用 RECORDED_RESOLUTION，Incident 状态与版本不变；
- 新 critical Alert 关联 high Incident 时只提升严重度和版本；
- 旧 job 版本保存 SUPERSEDED；
- 关联失败时 Incident、Link、Decision 零残留，任务随后按固定错误码重试；
- decision facts 只包含设计白名单，审计不含标题、摘要或 symptom 原始未知值。

- [ ] **步骤 5：增加同服务并发收敛测试**

使用 `Barrier` 同时处理两个同服务、同窗口 Alert。断言一个创建 Incident，另一个在目录行锁释放后关联该 Incident；最终一条 Incident、两条唯一 Link、两条 Decision，且没有 DiagnosisRun。若触发唯一约束，必须退出失败事务后有界重试。

- [ ] **步骤 6：运行关联服务与原有领域回归**

```bash
cd backend
II_TEST_DATABASE_URL="$II_MYSQL_BOOTSTRAP_URL" PYTHONPATH=. .venv/bin/pytest -q \
  tests/integration/services/test_correlation_service.py \
  tests/integration/services/test_signal_intake_service.py \
  tests/unit/domain/test_correlation.py
```

预期：完整决策矩阵、并发和零 DiagnosisRun 边界通过。

- [ ] **步骤 7：提交关联服务**

```bash
git add backend/src/incident_intelligence/persistence \
  backend/src/incident_intelligence/services/correlation.py \
  backend/tests/integration/services/test_correlation_service.py
git commit -m "feat: 创建并关联可解释事故"
```

---

### 任务 7：增加关联读取、重试 API 和后台 Runner

**文件：**

- 创建：`backend/src/incident_intelligence/api/schemas/correlation.py`
- 创建：`backend/src/incident_intelligence/api/routes/correlation.py`
- 创建：`backend/src/incident_intelligence/services/correlation_runner.py`
- 修改：`backend/src/incident_intelligence/api/dependencies.py`
- 修改：`backend/src/incident_intelligence/api/errors.py`
- 修改：`backend/src/incident_intelligence/api/router.py`
- 修改：`backend/src/incident_intelligence/main.py`
- 创建：`backend/tests/api/test_correlation.py`
- 创建：`backend/tests/unit/services/test_correlation_runner.py`

**接口：**

- 产生：`GET /api/v1/alerts/{id}/correlation`。
- 产生：`GET /api/v1/correlation/jobs`。
- 产生：`POST /api/v1/correlation/jobs/{id}/retry`。
- 产生：`CorrelationRunner.run_once()` 和可停止后台循环。

- [ ] **步骤 1：编写关联结果和失败重试失败 API 测试**

```python
def test_alert_correlation_response_is_bounded_and_explainable(client, seeded_decision):
    response = client.get(
        f"/api/v1/alerts/{seeded_decision.alert_id}/correlation",
        headers=manual_headers,
    )
    assert response.status_code == 200
    assert set(response.json()) == {"alert_id", "job", "incident", "decision"}
    assert response.json()["decision"]["rule_version"] == "correlation.v1"
    assert response.json()["decision"]["explanation"] == "窗口内没有同服务事故，已创建独立事故。"


def test_only_failed_job_can_be_retried(client, succeeded_job):
    response = client.post(f"/api/v1/correlation/jobs/{succeeded_job.id}/retry", headers=manual_headers)
    assert response.status_code == 409
    assert response.json()["code"] == "correlation_job_not_retryable"
```

其他来源 Token、无 Token 均返回 401；列表 `limit` 最大 100、offset 最大 10000；响应不得包含 lease_owner、异常正文、Alert 标题摘要、来源 URI或完整 facts。

- [ ] **步骤 2：运行 API 测试并确认路由 404**

```bash
cd backend
II_TEST_DATABASE_URL="$II_MYSQL_BOOTSTRAP_URL" PYTHONPATH=. \
  .venv/bin/pytest -q tests/api/test_correlation.py -x
```

预期：合法读取返回 404。

- [ ] **步骤 3：实现读取、列表、重试路由与错误映射**

路由使用 `require_manual_actor`。重试调用 `CorrelationJobService.retry_failed` 并追加 `correlation.job_retried` 有界审计。错误映射：不存在 404 `resource_not_found`，非 FAILED 返回 409 `correlation_job_not_retryable`，数据库不可用返回现有 503。

- [ ] **步骤 4：编写 Runner 生命周期和异常隔离失败测试**

```python
async def test_runner_failure_does_not_stop_loop_or_app():
    processor = FailsOnceThenSucceeds()
    runner = CorrelationRunner(job_service, processor, settings, sleeper=controlled_sleep)
    await runner.run_once()
    await runner.run_once()
    assert processor.calls == 2
    assert runner.last_cycle_error_code == "correlation_cycle_failed"
```

另测 `run_once` 按 batch_size 领取并处理、单任务失败不阻断同批其他任务、stop 后不再轮询、TestClient lifespan 启停不遗留线程。

- [ ] **步骤 5：实现后台 Runner 和 FastAPI lifespan**

`CorrelationRunner.run_once` 对同步数据库服务使用 `asyncio.to_thread`，每个 lease 独立处理并调用 job fail。后台 `run_forever` 捕获异常只记录固定错误码，不记录异常正文或业务输入。`create_app` 使用 `asynccontextmanager` lifespan：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if app.state.settings.correlation_runner_enabled:
        task = asyncio.create_task(app.state.correlation_runner.run_forever())
    try:
        yield
    finally:
        if task is not None:
            await app.state.correlation_runner.stop()
            await task
```

现有测试 settings 默认关闭 Runner；专用生命周期测试显式开启。

- [ ] **步骤 6：运行关联 API、Runner 与所有 API 回归**

```bash
cd backend
II_TEST_DATABASE_URL="$II_MYSQL_BOOTSTRAP_URL" PYTHONPATH=. .venv/bin/pytest -q \
  tests/api \
  tests/unit/services/test_correlation_runner.py
```

预期：读取、重试、Token 隔离、生命周期和现有接口全部通过。

- [ ] **步骤 7：提交关联 API 和 Runner**

```bash
git add backend/src/incident_intelligence/api \
  backend/src/incident_intelligence/services/correlation_runner.py \
  backend/src/incident_intelligence/main.py \
  backend/tests/api/test_correlation.py \
  backend/tests/unit/services/test_correlation_runner.py
git commit -m "feat: 运行并解释事故关联任务"
```

---

### 任务 8：端到端验收、真实冒烟与状态更新

**文件：**

- 修改：`README.md`
- 修改：`docs/architecture.md`
- 修改：`docs/current-state.md`
- 修改：`specs/active/multi-source-incident-center.md`
- 移动：`specs/active/service-catalog-correlation.md` → `specs/completed/service-catalog-correlation.md`
- 创建：`docs/verification/2026-08-25-service-catalog-correlation.md`

**接口：**

- 验收：真实 Alertmanager/CloudEvents → Alert → correlation job → Incident → 中文决策。
- 保持：DiagnosisRun、自动取证和 AI 仍未实现。

- [ ] **步骤 1：编写跨层验收测试**

创建 `backend/tests/integration/test_external_alert_to_incident.py`，通过真实服务顺序验证：

```python
def test_registered_high_production_alert_becomes_explainable_incident(context):
    context.catalog.create_service(payment_production)
    intake = context.signal_intake.submit_batch((cloud_firing,), "cloudevents-adapter", "req-1")
    leases = context.jobs.claim_batch("test-runner", NOW, limit=10, lease_seconds=30)
    decision = context.correlation.process(leases[0])
    assert decision.outcome == "CREATED_NO_MATCH"
    assert decision.explanation == "窗口内没有同服务事故，已创建独立事故。"
    assert context.count(IncidentRow) == 1
    assert context.count(DiagnosisRunRow) == 0
```

同文件增加第二个同服务 Alert 自动关联、一跳同症状创建独立事故候选、低严重度拒绝、resolved 不关闭 Incident、接入重放不追加任务。

- [ ] **步骤 2：运行跨层验收测试**

```bash
cd backend
II_TEST_DATABASE_URL="$II_MYSQL_BOOTSTRAP_URL" PYTHONPATH=. \
  .venv/bin/pytest -q tests/integration/test_external_alert_to_incident.py -x
```

预期：前序任务已经分别完成测试驱动实现；本步骤作为跨层验收，所有 Alert、任务、Incident、决策和零 DiagnosisRun 断言直接通过，不临时修改业务实现。

- [ ] **步骤 3：运行仓库统一验收**

```bash
II_TEST_DATABASE_URL="$II_MYSQL_BOOTSTRAP_URL" ./scripts/verify-backend.sh
```

预期：Ruff、格式、Mypy、迁移升级/降级、全部测试和覆盖率 90% 门槛零失败。

- [ ] **步骤 4：执行隔离临时库真实 HTTP 冒烟**

使用精确命名临时 MySQL 数据库和当前终端随机三套 Token，迁移到 `0002_service_catalog_correlation`，启动 Uvicorn。依次：

1. 通过目录 API 登记 payment-api/production；
2. 发送 high CloudEvent firing；
3. 轮询关联读取接口直到出现 CREATED_NO_MATCH；
4. 发送第二个同服务不同 alert_key firing，确认 LINKED_EXACT_SERVICE 且 Incident ID 相同；
5. 发送 resolved，确认 RECORDED_RESOLUTION 且 Incident 仍为 DETECTED；
6. 查询数据库确认 1 个 Incident、0 个 DiagnosisRun、3 个不可变决策；
7. 扫描任务、决策、关系和审计，原始 URI、查询、Token、禁止身份和异常正文均为 0；
8. 停止 Uvicorn 并精确删除临时数据库。

只记录状态码、资源 ID 是否一致、固定 outcome、Incident 状态和计数，不记录 Token 或完整请求。

- [ ] **步骤 5：更新事实文档和验收规格**

README 增加可直接替换资源 ID 的脱敏目录配置和关联结果读取示例；architecture 只把主数据流第 4–6 步的首版规则标为已实现；current-state 记录真实测试数、覆盖率、迁移版本、冒烟结果和已知缺口。验收通过后把聚焦规格状态改为“已验收”并移入 completed。

- [ ] **步骤 6：记录验收证据**

`docs/verification/2026-08-25-service-catalog-correlation.md` 必须记录：执行命令、测试数量、覆盖率、迁移往返、目录/关联规则矩阵、并发、租约、真实 HTTP 结论、安全扫描和以下缺口：无自动取证、无 DiagnosisRun 自动创建、无 AI、无人工合并拆分、无前端。

- [ ] **步骤 7：提交验收记录**

```bash
git add README.md docs specs backend/tests/integration/test_external_alert_to_incident.py
git commit -m "docs: 验收服务目录与事故关联"
```

- [ ] **步骤 8：最终验证 main 与清理状态**

```bash
git status --short --branch
git log --oneline -10
git branch --format='%(refname:short)'
git worktree list --porcelain
```

预期：只有 main、一个工作区、无临时数据库或进程、工作区干净；统一验收结果来自最终提交树。

## 计划自检

- **规格覆盖：** 任务 1 覆盖固定症状、门槛、结果和中文模板；任务 2 覆盖六表迁移、回填和约束；任务 3–4 覆盖版本化服务目录与管理 API；任务 5 覆盖任务原子性、租约和重试；任务 6 覆盖候选、Incident、关系、决策、并发和零 DiagnosisRun；任务 7 覆盖读取、人工重试和 Runner；任务 8 覆盖跨层、真实 HTTP、安全和诚实状态。
- **类型一致：** `CorrelationDecisionDraft`、`CorrelationJobLease`、六个 ORM Row、目录命令和异常均在首次使用任务中定义，后续任务只消费相同名称。
- **事务边界：** 接入事务只追加任务；关联事务原子写 Incident、Link、Decision 和 job 完成；失败重试在独立事务记录固定错误。
- **安全边界：** 决策、任务、审计和响应均使用白名单字段；设计禁止的 Secret、URI、查询、异常正文和实验身份都有测试和数据库扫描。
- **范围边界：** 本计划不创建 DiagnosisRun，不实现自动取证、AI、人工合并拆分、事故状态写接口或前端。
