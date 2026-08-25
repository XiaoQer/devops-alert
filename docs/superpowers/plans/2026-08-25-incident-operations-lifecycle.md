# 事故处置闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本项目明确禁止子 Agent、功能分支和 worktree，全部变更顺序执行并直接提交到 `main`。

**Goal:** 建立事故从认领、人工记录、状态推进、解决、重新打开到关闭的真实 MySQL 处置闭环，并在事故详情页提供已确认的中文操作界面。

**Architecture:** Incident 保存当前运营状态和乐观版本；新增不可变 `incident_activities` 保存用户可见时间线，新增 `incident_operations` 保存摘要化幂等结果。所有写操作由独立应用服务在一个 MySQL 事务内执行行锁、版本校验、状态机校验、状态更新、活动、审计和幂等结果写入；前端只依据 overview 返回的允许操作渲染按钮，写入成功后重新读取服务端事实。

**Tech Stack:** Python 3、FastAPI、Pydantic、SQLAlchemy、Alembic、MySQL 8.4、Vue 3、Vite、Vitest。

**Spec:** `specs/active/incident-operations-lifecycle.md`

## Global Constraints

- 全部变更直接提交到 `main`，禁止创建分支、worktree 或子 Agent。
- 所有后端行为先写失败测试，再实现最小完整改动；每个任务完成后单独提交。
- 浏览器不能上报操作者或负责人，actor 只能来自人工控制面认证上下文。
- 每个写接口必须同时要求 `Idempotency-Key` 和 `expected_version`。
- 原始幂等键、业务正文、Token、数据库错误和请求负载不得进入幂等表或安全审计。
- `scenario_id`、`scenario_version`、`experiment_id`、注入动作和标准答案不得进入请求、活动、审计、时间线或前端状态。
- 活动最多返回 200 条，关联告警继续最多返回 100 条；列表和详情查询必须有界且禁止 N+1。
- AI、数据源和关联 Runner 不可用不得阻塞人工处置。
- 前端不得乐观伪造成功状态；每次成功写入后重新读取列表和 overview。

---

### Task 1: 纯领域事故处置状态机

**Files:**
- Create: `backend/src/incident_intelligence/domain/incident_operations.py`
- Create: `backend/tests/unit/domain/test_incident_operations.py`
- Modify: `backend/src/incident_intelligence/domain/enums.py`

**Interfaces:**
- Produces: `IncidentActivityKind`、`IncidentOperationKind`、`IncidentNoteCategory`、`IncidentResolutionCategory`。
- Produces: `allowed_transitions(state: IncidentState) -> tuple[IncidentState, ...]`。
- Produces: `allowed_actions(state: IncidentState, assignee: str | None, actor: str) -> tuple[IncidentOperationKind, ...]`。
- Produces: `primary_action(state: IncidentState) -> IncidentPrimaryAction | None`。
- Produces: `require_transition(current: IncidentState, target: IncidentState) -> None`，非法时抛出 `InvalidIncidentTransition`。

- [x] **Step 1: 编写全部允许转换的失败测试**

```python
@pytest.mark.parametrize(
    ("current", "target"),
    [
        (IncidentState.DETECTED, IncidentState.TRIAGING),
        (IncidentState.DETECTED, IncidentState.INVESTIGATING),
        (IncidentState.TRIAGING, IncidentState.INVESTIGATING),
        (IncidentState.TRIAGING, IncidentState.MITIGATING),
        (IncidentState.INVESTIGATING, IncidentState.MITIGATING),
        (IncidentState.INVESTIGATING, IncidentState.MONITORING_RECOVERY),
        (IncidentState.MITIGATING, IncidentState.INVESTIGATING),
        (IncidentState.MITIGATING, IncidentState.MONITORING_RECOVERY),
        (IncidentState.MONITORING_RECOVERY, IncidentState.INVESTIGATING),
        (IncidentState.MONITORING_RECOVERY, IncidentState.MITIGATING),
    ],
)
def test_allows_only_declared_normal_transitions(current, target):
    assert target in allowed_transitions(current)
    require_transition(current, target)
```

- [x] **Step 2: 编写终态、解决专用路径和允许操作失败测试**

```python
def test_resolved_and_closed_are_not_normal_transition_targets():
    with pytest.raises(InvalidIncidentTransition):
        require_transition(IncidentState.INVESTIGATING, IncidentState.RESOLVED)
    assert allowed_transitions(IncidentState.CLOSED) == ()
    assert allowed_actions(IncidentState.CLOSED, "actor", "actor") == ()

def test_release_is_only_visible_to_current_assignee():
    assert IncidentOperationKind.RELEASE in allowed_actions(
        IncidentState.INVESTIGATING, "manual-api-client", "manual-api-client"
    )
    assert IncidentOperationKind.RELEASE not in allowed_actions(
        IncidentState.INVESTIGATING, "other-actor", "manual-api-client"
    )
```

- [x] **Step 3: 运行测试并确认因模块和枚举缺失失败**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/domain/test_incident_operations.py -q`

Expected: collection FAIL，明确指向 `incident_operations` 或新枚举不存在。

- [x] **Step 4: 实现固定枚举、转换表和主操作**

```python
NORMAL_TRANSITIONS: dict[IncidentState, tuple[IncidentState, ...]] = {
    IncidentState.DETECTED: (IncidentState.TRIAGING, IncidentState.INVESTIGATING),
    IncidentState.TRIAGING: (IncidentState.INVESTIGATING, IncidentState.MITIGATING),
    IncidentState.INVESTIGATING: (
        IncidentState.MITIGATING,
        IncidentState.MONITORING_RECOVERY,
    ),
    IncidentState.MITIGATING: (
        IncidentState.INVESTIGATING,
        IncidentState.MONITORING_RECOVERY,
    ),
    IncidentState.MONITORING_RECOVERY: (
        IncidentState.INVESTIGATING,
        IncidentState.MITIGATING,
    ),
    IncidentState.RESOLVED: (),
    IncidentState.CLOSED: (),
}

PRIMARY_ACTIONS = {
    IncidentState.DETECTED: IncidentPrimaryAction("transition", IncidentState.TRIAGING),
    IncidentState.TRIAGING: IncidentPrimaryAction("transition", IncidentState.INVESTIGATING),
    IncidentState.INVESTIGATING: IncidentPrimaryAction("transition", IncidentState.MITIGATING),
    IncidentState.MITIGATING: IncidentPrimaryAction(
        "transition", IncidentState.MONITORING_RECOVERY
    ),
    IncidentState.MONITORING_RECOVERY: IncidentPrimaryAction("resolve", None),
    IncidentState.RESOLVED: IncidentPrimaryAction("close", None),
}
```

`allowed_actions` 对所有未关闭活动状态返回 `ADD_NOTE`、`RESOLVE` 和适用的 `TRANSITION`；未认领时返回 `CLAIM`；仅当前 assignee 返回 `RELEASE`；`RESOLVED` 只返回 `REOPEN`、`CLOSE`；`CLOSED` 返回空元组。

- [x] **Step 5: 运行聚焦测试、Ruff 和 Mypy**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/domain/test_incident_operations.py -q && .venv/bin/python -m ruff check src tests && .venv/bin/python -m mypy src`

Expected: 全部通过。

- [x] **Step 6: 提交**

```bash
git add backend/src/incident_intelligence/domain/enums.py backend/src/incident_intelligence/domain/incident_operations.py backend/tests/unit/domain/test_incident_operations.py
git commit -m "feat: 定义事故处置状态机"
```

### Task 2: 事故活动、操作幂等与状态时间迁移

**Files:**
- Create: `backend/migrations/versions/0004_incident_operations_lifecycle.py`
- Modify: `backend/src/incident_intelligence/persistence/models.py`
- Modify: `backend/src/incident_intelligence/ids.py`
- Modify: `backend/tests/integration/persistence/test_correlation_migration.py`
- Modify: `backend/tests/integration/persistence/test_constraints.py`

**Interfaces:**
- Produces: `IncidentRow.state_changed_at`、`resolved_at`、`closed_at`。
- Produces: `IncidentActivityRow`，主键前缀 `iact_`。
- Produces: `IncidentOperationRow`，主键前缀 `iop_`，唯一键 `(scope, idempotency_key_hash)`。
- Consumes: Task 1 的固定枚举值作为数据库检查约束的单一语义来源。

- [x] **Step 1: 编写迁移结构与历史回填失败测试**

```python
def test_lifecycle_migration_backfills_current_state_times(alembic_config, mysql_engine):
    command.upgrade(alembic_config, "0003_incident_assignment")
    incident_id = seed_historical_incident(mysql_engine, state="RESOLVED")
    command.upgrade(alembic_config, "head")
    with Session(mysql_engine) as session:
        incident = session.get(IncidentRow, incident_id)
        assert incident.state_changed_at == incident.created_at
        assert incident.resolved_at == incident.created_at
        assert incident.closed_at is None
    assert {"incident_activities", "incident_operations"} <= set(
        inspect(mysql_engine).get_table_names()
    )
```

- [x] **Step 2: 编写数据库约束失败测试**

```python
def test_closed_incident_requires_resolved_and_closed_times(session, incident):
    incident.state = "CLOSED"
    incident.state_changed_at = NOW
    incident.resolved_at = None
    incident.closed_at = NOW
    with pytest.raises(IntegrityError):
        session.commit()

def test_operation_hash_and_fingerprint_are_fixed_length(session, incident):
    session.add(make_operation(incident.id, idempotency_key_hash="short"))
    with pytest.raises(IntegrityError):
        session.commit()
```

- [x] **Step 3: 运行迁移与约束测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/persistence/test_correlation_migration.py tests/integration/persistence/test_constraints.py -q`

Expected: FAIL，明确指向 `0004`、新表或新字段缺失。

- [x] **Step 4: 实现 `0004` 可逆迁移和 ORM**

迁移顺序：增加三个可空时间字段；按现有状态执行确定性回填；把 `state_changed_at` 改为非空；创建解决/关闭时间检查约束；创建 `incident_activities` 和 `incident_operations` 及索引。`downgrade` 先删除两张新表和检查约束，再删除三个时间字段。

```python
class IncidentActivityRow(Base):
    __tablename__ = "incident_activities"
    id: Mapped[str] = mapped_column(String(37), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str | None] = mapped_column(String(2_000), nullable=True)
    resolution_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution_actions: Mapped[str | None] = mapped_column(String(4_000), nullable=True)
    root_cause: Mapped[str | None] = mapped_column(String(4_000), nullable=True)
    incident_version: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
```

`IncidentOperationRow` 只保存固定字段：`id`、`scope`、64 字符幂等键摘要、64 字符请求指纹、Incident ID、操作类型、结果状态、可空结果 assignee、结果版本、活动 ID 和完成时间。

- [x] **Step 5: 运行迁移往返、ORM 一致性和约束测试**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/persistence/test_correlation_migration.py tests/integration/persistence/test_constraints.py -q`

Expected: 全部通过，`alembic check` 无差异。

- [x] **Step 6: 提交**

```bash
git add backend/migrations/versions/0004_incident_operations_lifecycle.py backend/src/incident_intelligence/persistence/models.py backend/src/incident_intelligence/ids.py backend/tests/integration/persistence/test_correlation_migration.py backend/tests/integration/persistence/test_constraints.py
git commit -m "feat: 增加事故处置持久化结构"
```

### Task 3: 单事务事故操作服务

**Files:**
- Create: `backend/src/incident_intelligence/persistence/incident_operation_repository.py`
- Create: `backend/src/incident_intelligence/services/incident_operations.py`
- Create: `backend/tests/integration/services/test_incident_operations.py`
- Modify: `backend/src/incident_intelligence/main.py`
- Modify: `backend/src/incident_intelligence/api/dependencies.py`

**Interfaces:**
- Produces: `IncidentOperationService`。
- Produces: `claim`、`release`、`transition`、`add_note`、`resolve`、`reopen`、`close` 方法；所有方法接受 `incident_id`、`expected_version`、`actor`、`idempotency_key`、`request_id`。
- Produces: `IncidentOperationResult(id, action, state, assignee, version, activity_id, occurred_at)`。
- Reuses: 现有 `IncidentResourceNotFound`，避免读取和写入服务产生两套未找到语义。
- Produces: `IncidentVersionConflict`、`IncidentOperationConflict`、`IncidentAlreadyClaimed`、`IncidentNotClaimed`、`IncidentClosed`、`InvalidIncidentOperation`。
- Consumes: Task 1 状态机与 Task 2 ORM。

- [x] **Step 1: 编写认领、解除认领和状态推进失败测试**

```python
def test_claim_release_and_transition_are_atomic(service, incident_id, session_factory):
    claimed = service.claim(
        incident_id, expected_version=1, actor="manual-api-client",
        idempotency_key="claim-1", request_id="req-claim",
    )
    transitioned = service.transition(
        incident_id, expected_version=2, target_state=IncidentState.INVESTIGATING,
        message="开始检查错误实例", actor="manual-api-client",
        idempotency_key="transition-1", request_id="req-transition",
    )
    released = service.release(
        incident_id, expected_version=3, actor="manual-api-client",
        idempotency_key="release-1", request_id="req-release",
    )
    assert (claimed.version, transitioned.version, released.version) == (2, 3, 4)
    assert persisted_activity_kinds(session_factory, incident_id) == (
        "INCIDENT_CLAIMED", "STATE_TRANSITIONED", "INCIDENT_RELEASED"
    )
```

- [x] **Step 2: 编写记录、解决、重新打开和关闭失败测试**

```python
def test_full_resolution_reopen_close_cycle(service, incident_id):
    note = service.add_note(
        incident_id, expected_version=1, category=IncidentNoteCategory.CURRENT_FINDING,
        message="错误集中在两个实例", actor="manual-api-client",
        idempotency_key="note-1", request_id="req-note",
    )
    resolved = service.resolve(
        incident_id, expected_version=note.version,
        category=IncidentResolutionCategory.RECOVERED,
        summary="错误率已恢复", actions="重启异常实例", root_cause=None,
        actor="manual-api-client", idempotency_key="resolve-1", request_id="req-resolve",
    )
    reopened = service.reopen(
        incident_id, expected_version=resolved.version, reason="错误率再次升高",
        actor="manual-api-client", idempotency_key="reopen-1", request_id="req-reopen",
    )
    assert reopened.state == "INVESTIGATING"
    assert load_incident(incident_id).resolved_at is None
```

继续在同一测试中再次解决并关闭，断言最终 `state == CLOSED`、`resolved_at` 与 `closed_at` 非空，且第一次解决活动仍存在。

- [x] **Step 3: 编写幂等、版本冲突、并发和回滚失败测试**

先定义七个独立用例，每个用例都创建自己的 Incident：`claim` 使用未认领 `DETECTED@v1`；`release` 使用已认领 `DETECTED@v2`；`transition` 使用 `DETECTED@v1` 进入 `TRIAGING`；`add_note` 使用 `DETECTED@v1`；`resolve` 使用 `DETECTED@v1`；`reopen` 使用 `RESOLVED@v2`；`close` 使用 `RESOLVED@v2`。对这七个用例参数化执行首次成功、相同请求精确重放、相同键不同正文冲突、陈旧版本零写入、两个独立 Session 并发只有一个成功，以及在活动/审计/幂等写入处注入异常后的完整回滚。

```python
def test_exact_replay_returns_first_result_without_duplicate_activity_or_audit(
    service, incident_id, session_factory
):
    command = {
        "incident_id": incident_id,
        "expected_version": 1,
        "category": IncidentNoteCategory.CURRENT_FINDING,
        "message": "错误集中在两个实例",
        "actor": "manual-api-client",
        "idempotency_key": "same-key",
        "request_id": "req-note",
    }
    first = service.add_note(**command)
    replay = service.add_note(**command)
    assert replay == first
    assert count_activities(session_factory, incident_id) == 1
    assert count_operation_audits(session_factory, incident_id) == 1

def test_same_key_with_different_payload_conflicts(service, incident_id):
    service.add_note(
        incident_id, expected_version=1,
        category=IncidentNoteCategory.CURRENT_FINDING, message="第一次内容",
        actor="manual-api-client", idempotency_key="same-key", request_id="req-first",
    )
    with pytest.raises(IncidentOperationConflict):
        service.add_note(
            incident_id, expected_version=1,
            category=IncidentNoteCategory.CURRENT_FINDING, message="第二次内容",
            actor="manual-api-client", idempotency_key="same-key", request_id="req-second",
        )

def test_stale_version_does_not_write_partial_rows(service, incident_id, session_factory):
    with pytest.raises(IncidentVersionConflict):
        service.transition(
            incident_id, expected_version=99, target_state=IncidentState.INVESTIGATING,
            message="开始调查", actor="manual-api-client",
            idempotency_key="stale-key", request_id="req-stale",
        )
    assert count_activities(session_factory, incident_id) == 0
    assert count_operations(session_factory, incident_id) == 0
    assert count_operation_audits(session_factory, incident_id) == 0
```

并发测试使用两个独立 Session 和 barrier 同时以相同 `expected_version` 操作，同一 Incident 只能有一个成功，另一个返回版本冲突；测试不得依赖线程执行顺序。

- [x] **Step 4: 运行测试确认服务缺失失败**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_incident_operations.py -q`

Expected: collection FAIL，明确指向服务或仓储缺失。

- [x] **Step 5: 实现仓储、规范化指纹和事务模板**

```python
class IncidentOperationService:
    def _execute(self, command: IncidentOperationCommand) -> IncidentOperationResult:
        key_hash = sha256(command.idempotency_key.encode()).hexdigest()
        fingerprint = command_fingerprint(command)
        try:
            return self._execute_once(command, key_hash, fingerprint)
        except IntegrityError as error:
            replay = self._replay_after_unique_conflict(command.scope, key_hash, fingerprint)
            if replay is None:
                raise error
            return replay
```

`_execute_once` 使用 `session_factory.begin()`，先查幂等结果，再 `SELECT FOR UPDATE` Incident，按固定顺序校验、更新、追加 `IncidentActivityRow`、调用现有 `RecordRepositories.add_audit`、追加 `IncidentOperationRow`。安全审计详情只允许 `reason_code` 和 `activity_id`。

- [x] **Step 6: 运行聚焦测试和后端静态检查**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_incident_operations.py -q && .venv/bin/python -m ruff check src tests migrations && .venv/bin/python -m ruff format --check src tests migrations && .venv/bin/python -m mypy src migrations`

Expected: 全部通过。

- [x] **Step 7: 提交**

```bash
git add backend/src/incident_intelligence/persistence/incident_operation_repository.py backend/src/incident_intelligence/services/incident_operations.py backend/src/incident_intelligence/main.py backend/src/incident_intelligence/api/dependencies.py backend/tests/integration/services/test_incident_operations.py
git commit -m "feat: 实现事故处置事务服务"
```

### Task 4: 事故处置 HTTP 契约

**Files:**
- Modify: `backend/src/incident_intelligence/api/schemas/incidents.py`
- Modify: `backend/src/incident_intelligence/api/routes/incidents.py`
- Modify: `backend/tests/api/test_incidents.py`

**Interfaces:**
- Produces: 七个规格中的 `POST /api/v1/incidents/{id}/*` 写接口。
- Produces: `IncidentOperationResponse`。
- Consumes: `require_manual_actor`、`require_idempotency_key` 和 Task 3 `IncidentOperationService`。

- [ ] **Step 1: 编写完整 HTTP 旅程失败测试**

```python
def post_operation(context, incident_id, suffix, version, body=None, key=None):
    return context.client.post(
        f"/api/v1/incidents/{incident_id}/{suffix}",
        headers={**context.manual_headers, "Idempotency-Key": key or f"{suffix}-{version}"},
        json={"expected_version": version, **(body or {})},
    )

def test_incident_operation_http_journey(context):
    incident_id = seed_linked_incident(context)
    claim = post_operation(context, incident_id, "claim", 1)
    investigate = post_operation(context, incident_id, "transitions", 2, {
        "target_state": "INVESTIGATING", "message": "开始调查"
    })
    note = post_operation(context, incident_id, "notes", 3, {
        "category": "CURRENT_FINDING", "message": "错误集中在两个实例"
    })
    assert [claim.status_code, investigate.status_code, note.status_code] == [200, 200, 200]
    assert note.json()["version"] == 4
```

旅程继续覆盖缓解、恢复观察、解决、重新打开、再次解决和关闭，逐步使用上一步响应版本。

- [ ] **Step 2: 编写认证、容量和稳定错误失败测试**

```python
@pytest.mark.parametrize("suffix", ["claim", "release", "transitions", "notes", "resolve", "reopen", "close"])
def test_all_operation_routes_require_manual_token_and_idempotency_key(context, suffix):
    assert post_without_token(context, suffix).status_code == 401
    assert post_without_key(context, suffix).json()["code"] == "invalid_idempotency_key"

def test_stale_version_returns_safe_conflict(context):
    response = post_operation(context, incident_id, "claim", 99)
    assert response.status_code == 409
    assert response.json() == {
        "code": "incident_version_conflict",
        "message": "事故已被其他操作更新，请刷新后重试",
    }
```

参数测试覆盖 1001 字符状态说明、2001 字符记录/说明、4001 字符措施/根因、非法枚举、浏览器自报 `actor` 或 `assignee` 产生 422。

- [ ] **Step 3: 运行 API 测试确认路由契约失败**

Run: `cd backend && .venv/bin/python -m pytest tests/api/test_incidents.py -q`

Expected: FAIL，明确指向请求模型、幂等头或路由缺失。

- [ ] **Step 4: 实现 Pydantic 请求、统一响应和异常映射**

```python
class IncidentOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)

class IncidentOperationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)
    id: str
    action: str
    state: str
    assignee: str | None
    version: int
    activity_id: str
    occurred_at: datetime
```

各操作使用独立请求模型，禁止宽泛 `dict`。路由统一调用 `_execute_operation` 映射 Task 3 稳定异常；格式错误 Incident ID 继续返回统一 404。

- [ ] **Step 5: 运行 API 测试及现有认证回归**

Run: `cd backend && .venv/bin/python -m pytest tests/api/test_incidents.py tests/api/test_resources.py tests/api/test_correlation.py -q`

Expected: 全部通过。

- [ ] **Step 6: 提交**

```bash
git add backend/src/incident_intelligence/api/schemas/incidents.py backend/src/incident_intelligence/api/routes/incidents.py backend/tests/api/test_incidents.py
git commit -m "feat: 提供事故处置写接口"
```

### Task 5: 有界业务活动与允许操作 Overview

**Files:**
- Modify: `backend/src/incident_intelligence/persistence/incident_center_repository.py`
- Modify: `backend/src/incident_intelligence/services/incident_center.py`
- Modify: `backend/src/incident_intelligence/api/schemas/incidents.py`
- Modify: `backend/src/incident_intelligence/api/routes/incidents.py`
- Create: `backend/tests/integration/services/test_incident_center.py`
- Modify: `backend/tests/api/test_incidents.py`

**Interfaces:**
- Produces: `IncidentActivityView`、`IncidentPrimaryActionView`。
- Changes: `IncidentCenterService.get_overview(incident_id: str, *, actor: str) -> IncidentOverview`。
- Produces overview 字段：`state_changed_at`、`resolved_at`、`closed_at`、`activities`、`activities_truncated`、`allowed_actions`、`allowed_transitions`、`primary_action`。
- Consumes: Task 1 状态机、Task 2 `IncidentActivityRow`。

- [ ] **Step 1: 编写活动截断、排序和允许操作失败测试**

```python
def test_overview_returns_bounded_activities_and_actor_specific_actions(service, seeded):
    seed_activities(seeded.incident_id, count=201)
    overview = service.get_overview(seeded.incident_id, actor="manual-api-client")
    assert len(overview.activities) == 200
    assert overview.activities_truncated is True
    assert list(overview.activities) == sorted(
        overview.activities, key=lambda item: (item.created_at, item.id)
    )
    assert "RELEASE" in overview.allowed_actions
    assert overview.primary_action.target_state == "TRIAGING"
```

- [ ] **Step 2: 编写列表最近活动和固定查询数失败测试**

添加一条晚于告警的 `NOTE_ADDED`，断言列表 `last_activity_at` 使用该活动时间。使用 SQLAlchemy 事件计数器分别读取 1 条和 200 条活动，overview 查询数必须相同。

- [ ] **Step 3: 运行聚焦测试确认视图字段缺失失败**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_incident_center.py tests/api/test_incidents.py -q`

- [ ] **Step 4: 实现活动固定查询、列表聚合和 actor 视图**

仓储新增 `activities(incident_id, limit=201)`；列表聚合增加每个 Incident 的活动最大时间子查询，并与关联告警最大时间、Incident 创建时间取最大值。Overview 只返回白名单列，活动正文按类型映射，不返回幂等摘要或审计详情。

```python
@router.get("/{incident_id}/overview", response_model=IncidentOverviewResponse)
def get_incident_overview(incident_id: str, actor: ManualActor, service: IncidentService):
    return IncidentOverviewResponse.model_validate(
        service.get_overview(incident_id, actor=actor), from_attributes=True
    )
```

- [ ] **Step 5: 运行聚焦与 API 测试**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_incident_center.py tests/api/test_incidents.py -q`

Expected: 全部通过，活动 200 条时 `activities_truncated=true`。

- [ ] **Step 6: 提交**

```bash
git add backend/src/incident_intelligence/persistence/incident_center_repository.py backend/src/incident_intelligence/services/incident_center.py backend/src/incident_intelligence/api/schemas/incidents.py backend/src/incident_intelligence/api/routes/incidents.py backend/tests/integration/services/test_incident_center.py backend/tests/api/test_incidents.py
git commit -m "feat: 扩展事故处置聚合详情"
```

### Task 6: 前端事故操作客户端与中文视图转换

**Files:**
- Modify: `frontend/src/api/incidents.js`
- Modify: `frontend/src/api/incidents.test.js`
- Modify: `frontend/src/presentation/incidentView.js`
- Modify: `frontend/src/presentation/incidentView.test.js`

**Interfaces:**
- Produces: `executeIncidentAction(incidentId, action, command, idempotencyKey, options)`。
- Produces: `toIncidentDetail` 对阶段、允许操作、主操作和活动的中文转换。
- Consumes: Task 4/5 HTTP 契约。

- [ ] **Step 1: 编写七种相对地址、幂等头和正文失败测试**

```javascript
it.each(["claim", "release", "transitions", "notes", "resolve", "reopen", "close"])(
  "%s 通过同源接口发送版本和幂等键",
  async (action) => {
    const request = vi.fn().mockResolvedValue(jsonResponse({ version: 2 }));
    vi.stubGlobal("fetch", request);
    await executeIncidentAction("inc_1", action, { expected_version: 1 }, "operation-key");
    expect(request.mock.calls[0][0]).toBe(`/api/v1/incidents/inc_1/${action}`);
    expect(request.mock.calls[0][1].headers).toMatchObject({
      "Content-Type": "application/json",
      "Idempotency-Key": "operation-key",
    });
    expect(request.mock.calls[0][1].headers.Authorization).toBeUndefined();
  },
);
```

- [ ] **Step 2: 编写活动和权限中文转换失败测试**

```javascript
expect(detail.allowedActions).toEqual(["添加处置记录", "推进状态", "解决事故"]);
expect(detail.primaryAction).toMatchObject({ label: "推进到分诊中", targetState: "TRIAGING" });
expect(detail.activities[0]).toMatchObject({
  title: "添加处置记录", category: "当前发现", actor: "当前操作员",
});
expect(JSON.stringify(detail)).not.toContain("manual-api-client");
```

- [ ] **Step 3: 运行前端聚焦测试确认函数或字段缺失失败**

Run: `cd frontend && npm test -- src/api/incidents.test.js src/presentation/incidentView.test.js`

- [ ] **Step 4: 实现通用安全写客户端和纯转换**

```javascript
export function executeIncidentAction(
  incidentId, action, command, idempotencyKey, options = {},
) {
  return requestJson(`/api/v1/incidents/${encodeURIComponent(incidentId)}/${action}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(command),
    signal: options.signal,
  });
}
```

转换层只翻译后端固定枚举；未知枚举显示“未知操作”而不猜测。活动 actor `manual-api-client` 显示为“当前操作员”，根因为空显示“尚未确认”。

- [ ] **Step 5: 运行聚焦测试**

Run: `cd frontend && npm test -- src/api/incidents.test.js src/presentation/incidentView.test.js`

Expected: 全部通过。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/api/incidents.js frontend/src/api/incidents.test.js frontend/src/presentation/incidentView.js frontend/src/presentation/incidentView.test.js
git commit -m "feat: 增加事故处置前端客户端"
```

### Task 7: 前端处置状态与重试语义

**Files:**
- Modify: `frontend/src/composables/useIncidentCenter.js`
- Create: `frontend/src/composables/useIncidentCenter.test.js`

**Interfaces:**
- Produces: `executeSelectedAction(action, payload)`、`retryLastAction()`、`refreshAfterConflict()`。
- Produces refs: `operationState`、`operationError`、`retryableOperation`。
- Consumes: Task 6 `executeIncidentAction`。

- [ ] **Step 1: 编写成功刷新和失败不伪造状态测试**

```javascript
it("写入成功后重新读取列表和详情", async () => {
  await center.executeSelectedAction("notes", {
    category: "CURRENT_FINDING", message: "错误集中在两个实例",
  });
  expect(executeIncidentAction).toHaveBeenCalledWith(
    "inc_1", "notes", expect.objectContaining({ expected_version: 4 }),
    expect.any(String), expect.any(Object),
  );
  expect(fetchIncidents).toHaveBeenCalledTimes(2);
  expect(fetchIncidentOverview).toHaveBeenCalledTimes(2);
});

it("网络结果未知时保留同一个幂等键用于重试", async () => {
  executeIncidentAction.mockRejectedValueOnce(new IncidentApiError(
    "incident_api_unavailable", "事故操作结果暂时未知"
  ));
  await center.executeSelectedAction("claim", {});
  const firstKey = executeIncidentAction.mock.calls[0][3];
  await center.retryLastAction();
  expect(executeIncidentAction.mock.calls[1][3]).toBe(firstKey);
});
```

- [ ] **Step 2: 编写版本冲突刷新测试**

冲突响应不得自动重放旧操作；`refreshAfterConflict` 只重新读取服务端列表和详情，清除待重试命令，并显示最新版本。

- [ ] **Step 3: 运行聚焦测试确认状态方法缺失失败**

Run: `cd frontend && npm test -- src/composables/useIncidentCenter.test.js`

- [ ] **Step 4: 实现单一在途操作、稳定键和刷新**

使用 `crypto.randomUUID()` 生成非 Secret 幂等键；一次用户动作只生成一次。网络不可用保留 `{action, command, key}`，明确业务 4xx/409 不保留重试；组件卸载时取消请求但不在浏览器持久化命令或 key。

- [ ] **Step 5: 运行组合式状态和现有页面回归测试**

Run: `cd frontend && npm test -- src/composables/useIncidentCenter.test.js src/App.test.js`

Expected: 全部通过。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/composables/useIncidentCenter.js frontend/src/composables/useIncidentCenter.test.js
git commit -m "feat: 管理事故处置前端状态"
```

### Task 8: 已确认的事故处置详情界面

**Files:**
- Create: `frontend/src/components/IncidentStageBar.vue`
- Create: `frontend/src/components/IncidentActivityTimeline.vue`
- Create: `frontend/src/components/IncidentQuickNote.vue`
- Create: `frontend/src/components/IncidentResolveDialog.vue`
- Create: `frontend/src/components/IncidentOperationDialog.vue`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/App.test.js`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Components consume only `toIncidentDetail` 的中文视图模型，不直接调用 HTTP。
- Components emit: `operate({ action, payload })`、`refresh()`。
- App consumes Task 7 `executeSelectedAction`、`retryLastAction`、`refreshAfterConflict`。

- [ ] **Step 1: 编写阶段栏、快速记录和允许操作失败测试**

```javascript
it("按后端允许操作展示调查阶段和主推进按钮", async () => {
  const wrapper = mount(App);
  await flushPromises();
  expect(wrapper.get('[data-testid="incident-stage-bar"]').text()).toContain("调查中");
  expect(wrapper.get('[data-testid="primary-operation"]').text()).toBe("推进到缓解中");
  expect(wrapper.find('[data-testid="release-incident"]').exists()).toBe(true);
  expect(wrapper.find('[data-testid="reopen-incident"]').exists()).toBe(false);
});

it("快速记录提交固定分类和真实版本", async () => {
  await wrapper.get('[aria-label="处置记录分类"]').setValue("CURRENT_FINDING");
  await wrapper.get('[aria-label="处置记录内容"]').setValue("错误集中在两个实例");
  await wrapper.get('[data-testid="save-note"]').trigger("click");
  expect(executeIncidentAction).toHaveBeenCalledWith(
    expect.anything(), "notes", expect.objectContaining({
      expected_version: 4, category: "CURRENT_FINDING",
    }), expect.anything(), expect.anything(),
  );
});
```

- [ ] **Step 2: 编写解决、重新打开、关闭和只读终态失败测试**

解决弹窗测试必须证明根因可以为空，但分类、说明和措施为空时按钮禁用；`RESOLVED` 只展示重新打开和关闭；`CLOSED` 不渲染任何写按钮或快速记录表单。

- [ ] **Step 3: 编写活动时间线和错误恢复失败测试**

时间线显示状态变化、分类、操作人、说明和解决内容；不显示原始枚举或 `manual-api-client`。版本冲突显示“事故已被其他操作更新”和刷新按钮；网络结果未知显示“重试本次操作”，重试复用 Task 7 key。

- [ ] **Step 4: 运行页面测试确认组件缺失失败**

Run: `cd frontend && npm test -- src/App.test.js`

- [ ] **Step 5: 实现小组件和页面编排**

`IncidentStageBar` 展示七阶段、当前阶段、`primaryAction` 和 `allowedTransitions`；回退状态不伪造历史完成标记，只高亮当前状态。`IncidentActivityTimeline` 按服务端顺序渲染活动。弹窗关闭时清空未提交正文，禁止在日志或 URL 中保存内容。

- [ ] **Step 6: 运行完整前端测试与构建**

Run: `cd frontend && npm test && npm run build && npm run test:sites`

Expected: Vitest、Vite 构建和 Sites Worker 全部通过；构建产物不包含 Token。

- [ ] **Step 7: 提交**

```bash
git add frontend/src/components frontend/src/App.vue frontend/src/App.test.js frontend/src/styles.css
git commit -m "feat: 实现事故处置闭环页面"
```

### Task 9: 真实 MySQL、浏览器全旅程与规格验收

**Files:**
- Modify: `README.md`
- Modify: `docs/current-state.md`
- Move: `specs/active/incident-operations-lifecycle.md` → `specs/completed/incident-operations-lifecycle.md`

**Interfaces:**
- Consumes: Tasks 1–8 的完整后端和前端能力。
- Produces: 可重复的真实验收证据和最终能力边界。

- [ ] **Step 1: 运行统一后端验收**

使用项目独立 MySQL 8.4 测试容器和随机临时密码：

```bash
ii_verify_password=$(openssl rand -hex 24)
export II_MYSQL_TEST_ROOT_PASSWORD="$ii_verify_password"
export II_MYSQL_TEST_PORT=43306
export II_TEST_DATABASE_URL="mysql+pymysql://root:$ii_verify_password@127.0.0.1:43306/mysql"
docker compose up -d --wait mysql-test
./scripts/verify-backend.sh
docker compose down -v
```

Expected: Ruff、格式、Mypy、全部 Pytest 和覆盖率门槛通过；精确测试容器与测试卷被清理。

- [ ] **Step 2: 运行最终前端验收和安全扫描**

Run: `cd frontend && npm test && npm run build && npm run test:sites`

Run: `rg -n "scenario_id|scenario_version|experiment_id|manual-api-client|II_FRONTEND_API_TOKEN" frontend/dist/client backend/src/incident_intelligence/api/schemas/incidents.py`

Expected: 测试与构建通过；扫描 0 命中。

- [ ] **Step 3: 升级本地项目 MySQL 并启动真实服务**

只通过当前终端环境提供本地数据库 URL 和三个随机 Token，执行 `alembic upgrade head`。前端继续通过 Vite 同源代理注入人工 Token；不得把 Token 写入文件、命令输出或浏览器。

- [ ] **Step 4: 使用公开 API 形成真实事故并完成浏览器旅程**

通过服务目录和 CloudEvents 正式入口创建一个本地联调事故，不直接写业务表。浏览器依次完成：

```text
认领 → 调查中 → 添加“当前发现” → 缓解中 → 恢复观察
→ 解决（根因留空）→ 重新打开 → 再次解决 → 关闭
```

每一步检查页面状态、负责人、版本和活动时间线；最终用只读 SQL 确认 Incident 为 `CLOSED`、活动数量与操作一致、每个动作只有一条审计和幂等记录。浏览器控制台 error/warn 必须为空。

- [ ] **Step 5: 验证失败与冲突页面**

在后端停止时尝试新增记录，页面必须保留只读详情、明确显示未完成且不得变化状态；恢复后使用同一幂等键重试。再用旧版本触发一次 409，页面必须提示刷新并读取最新事实，不自动重复旧操作。

- [ ] **Step 6: 更新文档和归档规格**

`docs/current-state.md` 只记录实际通过的状态、操作、测试数量、覆盖率和浏览器证据；README 增加写接口和本地使用说明，但不写凭据或联调业务正文。把规格状态改为“已完成并归档”，补充每条验收证据后移入 `specs/completed/`。

- [ ] **Step 7: 最终检查并提交**

```bash
git diff --check
git status --short --branch
git add README.md docs/current-state.md specs/active/incident-operations-lifecycle.md specs/completed/incident-operations-lifecycle.md
git commit -m "docs: 完成事故处置闭环验收"
git status --short --branch
```

Expected: `main` 工作区干净，最终提交只包含文档与规格归档。
