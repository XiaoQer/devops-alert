# Incident 自动生成、运营与飞书协同实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本仓库禁止子 Agent，所有任务在当前会话、`main` 分支顺序实施。

**Goal:** 让已发布 Incident 规则异步作用于真实 Alert，可靠创建和运营正式 Incident，并通过飞书企业自建应用完成通知、卡片操作和群聊时间线回流。

**Architecture:** Alert 接收事务只保存 Alert 与持久化评估任务，后台评估器复用现有纯规则函数并在 MySQL 中收敛 Incident；Incident 变化再写入独立通知 Outbox，由飞书处理器有界重试。页面、飞书卡片和群消息都通过同一个 Incident 状态机与不可变活动时间线协作，任何外部系统故障均不得回滚 Alert 或 Incident 事实。

**Tech Stack:** Python 3.13+、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、MySQL 8.4、Vue 3、Vitest、飞书开放平台 HTTP API。

**Spec:** `specs/active/incident-operations-and-feishu-collaboration.md`；详细设计：`docs/superpowers/specs/2026-09-01-incident-operations-and-feishu-collaboration-design.md`

## Global Constraints

- 所有变更直接在 `main` 分支完成，不创建分支、worktree 或子 Agent。
- 第一版不提供候选 Incident、跨规则合并、AI 聚类、自动取证、自动修复或自动解决。
- 进行中唯一边界固定为 `incident_rule_id + environment + group_key`；`OPEN` 与 `ACKNOWLEDGED` 都属于未解决。
- Incident 状态只允许 `OPEN → ACKNOWLEDGED → RESOLVED` 或 `OPEN → RESOLVED`；`RESOLVED` 不重开。
- Alert 全部恢复只记录事实，不自动解决 Incident；严重级别只自动升级，不自动降级。
- Alert 接收、Incident 评估和飞书通知使用独立持久化任务；后两者失败不得阻断接收。
- 飞书 Secret 仅从 `II_FEISHU_APP_ID`、`II_FEISHU_APP_SECRET`、`II_FEISHU_VERIFICATION_TOKEN`、`II_FEISHU_ENCRYPT_KEY` 读取，不进入数据库、日志、响应、快照或测试数据。
- 每个环境最多一个启用飞书群路由；普通群聊文本不得推断状态、负责人、严重级别或根因。
- 所有文本、JSON、批次、扫描条数、重试次数和 API 分页均有明确上限。
- 不读取故障注入平台，不使用 `scenario_id`、`scenario_version`、`experiment_id` 或任何实验身份。
- 前端不生成演示数据，视觉实现以已确认的 Incident 列表、三栏详情和飞书卡片产品图为准。
- 旧迁移已占用的 `incidents`、`incident_activities`、`incident_alert_links` 只保留兼容；新模型使用 `operational_incidents`、`operational_incident_activities`、`operational_incident_alerts`，运行时不得混用。

---

### Task 1：建立 Incident 领域模型、状态机与活动事实

**Files:**
- Create: `backend/src/incident_intelligence/domain/incidents.py`
- Modify: `backend/src/incident_intelligence/ids.py`
- Test: `backend/tests/unit/domain/test_incidents.py`

**Interfaces:**
- Produces: `IncidentState`、`Incident`、`IncidentActivity`、`IncidentAlertFact`、`IncidentAlertLink`、`IncidentActivityIds`。
- Produces: `create_incident(..., alerts, created_activity_id) -> IncidentChange`、`link_alerts(..., existing_alert_ids, alerts, activity_ids) -> IncidentChange`、`acknowledge_incident(..., activity_id) -> IncidentChange`、`resolve_incident(..., activity_id) -> IncidentChange`；领域层不随机生成活动 ID。
- ID prefixes: `inc`、`iact`、`iej`、`ino`、`inr`、`ift`、`fer`。

- [ ] **Step 1: 编写状态机和聚合失败测试**

```python
def test_create_incident_builds_deterministic_title_and_created_activity():
    change = create_incident(
        incident_id="inc_" + "1" * 32,
        reference="INC-20260901-001",
        rule_id="irl_" + "2" * 32,
        rule_version=3,
        environment="production",
        group_by="SERVICE",
        group_key="checkout",
        group_display_name="checkout",
        severity="high",
        alerts=(active_alert("alt_" + "3" * 32),),
        created_activity_id="iact_" + "4" * 32,
        now=NOW,
    )
    assert change.incident.title == "production checkout异常"
    assert change.incident.state == "OPEN"
    assert [item.kind for item in change.activities] == ["INCIDENT_CREATED"]


def test_link_alerts_is_idempotent_and_only_escalates_severity():
    first = link_alerts(make_incident(severity="high"), existing_alert_ids=(), alerts=(critical_alert(),), now=NOW)
    replay = link_alerts(first.incident, existing_alert_ids=(critical_alert().id,), alerts=(critical_alert(),), now=NOW)
    assert first.incident.severity == "critical"
    assert replay.changed is False


def test_resolved_incident_is_terminal_and_resolution_requires_note():
    with pytest.raises(IncidentStateConflict, match="resolution_summary_required"):
        resolve_incident(make_incident(), resolution_summary="", actor="tester", now=NOW)
    resolved = resolve_incident(make_incident(), resolution_summary="服务已恢复", actor="tester", now=NOW)
    with pytest.raises(IncidentStateConflict, match="incident_already_resolved"):
        acknowledge_incident(resolved.incident, actor="tester", now=NOW)
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/domain/test_incidents.py -q`

Expected: FAIL，提示 `incident_intelligence.domain.incidents` 不存在。

- [ ] **Step 3: 实现冻结领域模型和纯状态转换**

```python
IncidentState = Literal["OPEN", "ACKNOWLEDGED", "RESOLVED"]
IncidentActivityKind = Literal[
    "INCIDENT_CREATED", "ALERTS_LINKED", "SEVERITY_ESCALATED",
    "ALL_ALERTS_RECOVERED", "ACKNOWLEDGED", "RESOLVED",
    "FEISHU_MESSAGE_RECORDED", "NOTIFICATION_FAILED",
]

class Incident(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    reference: str
    title: str
    state: IncidentState
    severity: Severity
    environment: Environment
    group_by: RuleGroupBy
    group_key: str
    group_display_name: str
    incident_rule_id: str
    incident_rule_version: int
    alert_count: int
    active_alert_count: int
    distinct_alert_name_count: int
    version: int
    opened_at: UtcAwareDatetime
    acknowledged_at: UtcAwareDatetime | None
    resolved_at: UtcAwareDatetime | None
    resolution_summary: str | None
    last_alert_at: UtcAwareDatetime
    created_at: UtcAwareDatetime
    updated_at: UtcAwareDatetime
```

所有转换返回 `IncidentChange(incident, activities, new_alert_ids, changed)`；解决说明限制 1–2,000 字，活动摘要限制 1–500 字，元数据只接收有界标量。

- [ ] **Step 4: 运行领域测试并提交**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/domain/test_incidents.py -q`

Expected: PASS。

Commit: `git add backend/src/incident_intelligence/domain/incidents.py backend/src/incident_intelligence/ids.py backend/tests/unit/domain/test_incidents.py && git commit -m "feat: 建立 Incident 领域模型"`

### Task 2：建立 MySQL 模型、约束与 Incident 仓储

**Files:**
- Create: `backend/migrations/versions/0013_incident_operations_and_feishu.py`
- Modify: `backend/src/incident_intelligence/persistence/models.py`
- Create: `backend/src/incident_intelligence/persistence/incident_repository.py`
- Modify: `backend/src/incident_intelligence/persistence/unit_of_work.py`
- Test: `backend/tests/integration/persistence/test_incident_operations_schema.py`
- Test: `backend/tests/integration/services/test_incident_repository.py`

**Interfaces:**
- Produces: `IncidentRepository.insert/get/list/find_unresolved/link_alerts/append_activities/update/count_alert_states`。
- Produces: `IncidentEvaluationJobRepository.enqueue/lease/complete/retry/fail`。
- Produces: `IncidentNotificationRepository.enqueue/lease/succeed/retry/fail`。
- Produces: `IncidentNotificationRouteRepository`、`IncidentFeishuThreadRepository`、`FeishuEventReceiptRepository`。

- [ ] **Step 1: 编写迁移失败测试**

```python
def test_incident_schema_contains_all_durable_boundaries(migrated_engine):
    names = set(inspect(migrated_engine).get_table_names())
    assert {
        "operational_incidents", "operational_incident_alerts",
        "operational_incident_activities",
        "incident_evaluation_jobs", "incident_notification_routes",
        "incident_notification_outbox", "incident_feishu_threads",
        "feishu_event_receipts",
    } <= names


def test_only_one_enabled_route_per_environment(session):
    session.add_all([enabled_route("production", "chat-a"), enabled_route("production", "chat-b")])
    with pytest.raises(IntegrityError):
        session.commit()
```

- [ ] **Step 2: 运行迁移测试并确认表不存在**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/persistence/test_incident_operations_schema.py -q`

Expected: FAIL，缺少 `incidents` 等表。

- [ ] **Step 3: 实现迁移和 ORM 行模型**

迁移必须建立以下数据库保护：

```text
incidents.open_boundary_key: 未解决时为 sha256(rule_id|environment|group_key)，解决时为 NULL；唯一索引保证并发收敛
incident_alerts: UNIQUE(incident_id, alert_id)
incident_evaluation_jobs: UNIQUE(alert_id, alert_version)
incident_notification_routes: enabled_environment_key 在启用时为 environment，停用时为 NULL；唯一索引
incident_notification_outbox: UNIQUE(notification_key)
incident_feishu_threads: UNIQUE(incident_id), UNIQUE(chat_id, root_message_id)
feishu_event_receipts: PRIMARY KEY(event_id)
```

任务状态统一为 `PENDING/LEASED/SUCCEEDED/FAILED`，保存 `attempt_count`、`available_at`、`lease_expires_at`、`last_error_code`；正文 JSON 和纯文本字段设置数据库长度约束。

- [ ] **Step 4: 编写仓储失败测试**

```python
def test_find_unresolved_locks_business_boundary(repository):
    repository.insert(make_incident(open_boundary_key=BOUNDARY))
    found = repository.find_unresolved(
        rule_id=RULE_ID, environment="production", group_key="checkout", for_update=True
    )
    assert found.id == INCIDENT_ID


def test_link_alerts_and_outbox_are_idempotent(repository):
    assert repository.link_alerts(INCIDENT_ID, (ALERT_ID,)) == (ALERT_ID,)
    assert repository.link_alerts(INCIDENT_ID, (ALERT_ID,)) == ()
    assert repository.enqueue_notification(notification("created")) is True
    assert repository.enqueue_notification(notification("created")) is False
```

- [ ] **Step 5: 实现仓储和工作单元接线**

```python
class SqlAlchemyUnitOfWork:
    incidents: IncidentRepository | None
    incident_evaluation_jobs: IncidentEvaluationJobRepository | None
    incident_notifications: IncidentNotificationRepository | None
    incident_notification_routes: IncidentNotificationRouteRepository | None
    incident_feishu_threads: IncidentFeishuThreadRepository | None
    feishu_event_receipts: FeishuEventReceiptRepository | None
```

仓储写入均调用 `flush()`，乐观更新使用 `WHERE id=:id AND version=:expected_version`，租约使用 `SELECT ... FOR UPDATE SKIP LOCKED`；MySQL 不支持所需锁语义时测试必须失败，不回退到进程锁。

- [ ] **Step 6: 运行持久化测试并提交**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/persistence/test_incident_operations_schema.py tests/integration/services/test_incident_repository.py -q`

Expected: PASS。

Commit: `git add backend/migrations/versions/0013_incident_operations_and_feishu.py backend/src/incident_intelligence/persistence/models.py backend/src/incident_intelligence/persistence/incident_repository.py backend/src/incident_intelligence/persistence/unit_of_work.py backend/tests/integration/persistence/test_incident_operations_schema.py backend/tests/integration/services/test_incident_repository.py && git commit -m "feat: 持久化 Incident 与异步任务"`

### Task 3：在 Alert 接收事务内可靠写入评估任务

**Files:**
- Modify: `backend/src/incident_intelligence/services/signal_intake.py`
- Test: `backend/tests/integration/services/test_signal_intake_service.py`

**Interfaces:**
- Consumes: `IncidentEvaluationJobRepository.enqueue(alert_id, alert_version, available_at)`。
- Produces: 每个非重放、实际改变 Alert 投影的输入恰好一个 `(alert_id, alert_version)` 评估任务。

- [ ] **Step 1: 编写事务边界失败测试**

```python
def test_alert_change_enqueues_evaluation_job_in_same_transaction(service, session_factory):
    result = service.submit_batch((firing_command(),), actor="test", request_id="req-1")
    with session_factory() as session:
        jobs = session.scalars(select(IncidentEvaluationJobRow)).all()
    assert [(job.alert_id, job.alert_version) for job in jobs] == [
        (result.items[0].alert_id, 1)
    ]


def test_exact_signal_replay_does_not_enqueue_another_job(service, session_factory):
    service.submit_batch((firing_command(),), actor="test", request_id="req-1")
    service.submit_batch((firing_command(),), actor="test", request_id="req-2")
    assert count_rows(session_factory, IncidentEvaluationJobRow) == 1


def test_job_insert_failure_rolls_back_signal_and_alert(service, monkeypatch, session_factory):
    monkeypatch.setattr(IncidentEvaluationJobRepository, "enqueue", raise_integrity_error)
    with pytest.raises(IntegrityError):
        service.submit_batch((firing_command(),), actor="test", request_id="req-1")
    assert count_rows(session_factory, AlertRow) == 0
```

- [ ] **Step 2: 运行目标测试并确认当前没有任务**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_signal_intake_service.py -q`

Expected: FAIL，评估任务数量为 0。

- [ ] **Step 3: 在现有 `_submit_command` 事务中写任务**

```python
if not replayed and projection.outcome in {
    "opened", "updated", "resolved", "reopened", "orphan_resolved"
}:
    evaluation_jobs.enqueue(
        alert_id=projection.alert.id,
        alert_version=projection.alert.version,
        available_at=now,
    )
```

`submit_batch` 的响应模型保持不变；不得同步调用规则评估器或飞书客户端。

- [ ] **Step 4: 运行接收与适配器回归测试并提交**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_signal_intake_service.py tests/api/test_alertmanager_intake.py tests/api/test_cloudevents_intake.py -q`

Expected: PASS。

Commit: `git add backend/src/incident_intelligence/services/signal_intake.py backend/tests/integration/services/test_signal_intake_service.py && git commit -m "feat: 为 Alert 变化写入 Incident 评估任务"`

### Task 4：实现实时规则评估、Incident 收敛与评估 Runner

**Files:**
- Create: `backend/src/incident_intelligence/services/incident_evaluation.py`
- Create: `backend/src/incident_intelligence/services/incident_evaluation_runner.py`
- Modify: `backend/src/incident_intelligence/persistence/alert_center_repository.py`
- Modify: `backend/src/incident_intelligence/persistence/incident_rule_repository.py`
- Modify: `backend/src/incident_intelligence/main.py`
- Modify: `backend/src/incident_intelligence/settings.py`
- Test: `backend/tests/integration/services/test_incident_evaluation_service.py`
- Test: `backend/tests/unit/services/test_incident_evaluation_runner.py`

**Interfaces:**
- Produces: `IncidentEvaluationService.process(job_id: str) -> EvaluationJobResult`。
- Produces: `IncidentEvaluationRunner.run_once(limit: int = 20) -> RunnerBatchResult`。
- Consumes: 现有 `evaluate_rule(config, alerts, max_matches=100)`，不得复制规则语义。

- [ ] **Step 1: 编写实时与历史语义一致性失败测试**

```python
def test_published_rule_hit_creates_formal_incident(service, published_rule, three_alerts):
    result = service.process(job_for(three_alerts[-1]))
    assert result.outcome == "INCIDENT_CREATED"
    assert result.incident_ids == (resulting_incident_id(),)
    assert linked_alert_ids(resulting_incident_id()) == {alert.id for alert in three_alerts}


def test_disabled_and_draft_rules_are_not_evaluated(service, draft_rule, disabled_rule, alert):
    result = service.process(job_for(alert))
    assert result.outcome == "NO_MATCH"
    assert result.reason_codes == ("no_published_rule_match",)


def test_resolved_boundary_creates_new_incident(service, resolved_incident, matching_alert):
    result = service.process(job_for(matching_alert))
    assert result.incident_ids != (resolved_incident.id,)
```

- [ ] **Step 2: 运行服务测试并确认服务不存在**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_incident_evaluation_service.py -q`

Expected: FAIL，缺少 `IncidentEvaluationService`。

- [ ] **Step 3: 实现有界实时评估服务**

```python
class IncidentEvaluationService:
    def process(self, job_id: str) -> EvaluationJobResult:
        # 锁定任务，读取锚点 Alert 快照，最多读取 100 条 PUBLISHED 规则；
        # 每条规则最多读取 10_001 条窗口 Alert；复用 evaluate_rule；
        # 对包含锚点 Alert 的命中做 create-or-update；写活动与通知 outbox；
        # 在同一事务内完成任务。
```

创建编号通过数据库日序列生成 `INC-YYYYMMDD-NNN`；并发唯一键冲突后重新读取现存未解决 Incident 并关联，不新建第二条。恢复事件会重算 Incident 下关联 Alert 的活动数；首次降为 0 时写一次 `ALL_ALERTS_RECOVERED`，状态保持原值。

- [ ] **Step 4: 编写并发、恢复和 Runner 失败测试**

```python
def test_two_jobs_for_same_boundary_converge_to_one_incident(concurrent_service, matching_jobs):
    run_concurrently(concurrent_service.process, matching_jobs)
    assert count_unresolved_incidents(BOUNDARY) == 1


def test_all_alerts_recovered_records_once_without_resolving(service, resolved_alert_jobs):
    for job in resolved_alert_jobs:
        service.process(job.id)
    incident = get_incident()
    assert incident.state == "OPEN"
    assert activity_kinds(incident.id).count("ALL_ALERTS_RECOVERED") == 1


def test_runner_retries_transient_failure_and_fails_after_limit(runner, jobs, clock):
    assert runner.run_once().retried == 1
    clock.advance(minutes=5)
    assert exhaust_attempts(runner, jobs[0]).state == "FAILED"
```

- [ ] **Step 5: 实现 Runner 与应用生命周期**

`Settings` 增加 `incident_workers_enabled: bool = True`、轮询间隔 1–60 秒、租约 10–300 秒、最大尝试 1–10、单批 1–100。FastAPI lifespan 创建一个评估循环和一个通知循环；关闭时先发停止信号，再等待当前批次结束，测试环境可关闭循环并手动 `run_once()`。

- [ ] **Step 6: 运行评估测试并提交**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_incident_evaluation_service.py tests/unit/services/test_incident_evaluation_runner.py -q`

Expected: PASS。

Commit: `git add backend/src/incident_intelligence/services/incident_evaluation.py backend/src/incident_intelligence/services/incident_evaluation_runner.py backend/src/incident_intelligence/persistence/alert_center_repository.py backend/src/incident_intelligence/persistence/incident_rule_repository.py backend/src/incident_intelligence/main.py backend/src/incident_intelligence/settings.py backend/tests/integration/services/test_incident_evaluation_service.py backend/tests/unit/services/test_incident_evaluation_runner.py && git commit -m "feat: 异步生成并收敛 Incident"`

### Task 5：实现 Incident 查询、确认和解决 API

**Files:**
- Create: `backend/migrations/versions/0015_operational_incident_operations.py`
- Create: `backend/src/incident_intelligence/services/incidents.py`
- Create: `backend/src/incident_intelligence/api/schemas/incidents.py`
- Create: `backend/src/incident_intelligence/api/routes/incidents.py`
- Modify: `backend/src/incident_intelligence/persistence/models.py`
- Modify: `backend/src/incident_intelligence/persistence/incident_repository.py`
- Modify: `backend/src/incident_intelligence/api/dependencies.py`
- Modify: `backend/src/incident_intelligence/api/router.py`
- Modify: `backend/src/incident_intelligence/main.py`
- Test: `backend/tests/integration/services/test_incident_service.py`
- Test: `backend/tests/api/test_incidents.py`

**Interfaces:**
- Produces: `IncidentService.list/get/acknowledge/resolve`。
- Produces: `GET /api/v1/incidents`、`GET /api/v1/incidents/{id}`、`POST /acknowledge`、`POST /resolve`。
- Writes require: Bearer Token、`Idempotency-Key`、`expected_version`；解决还要求 `resolution_summary`。

- [x] **Step 1: 编写应用服务失败测试**

```python
def test_acknowledge_is_idempotent_and_writes_one_activity(service):
    first = service.acknowledge(INCIDENT_ID, expected_version=1, idempotency_key="ack-1", actor="tester", request_id="req-1")
    replay = service.acknowledge(INCIDENT_ID, expected_version=1, idempotency_key="ack-1", actor="tester", request_id="req-2")
    assert replay.replayed is True
    assert count_activity(INCIDENT_ID, "ACKNOWLEDGED") == 1


def test_resolve_rejects_stale_version(service):
    with pytest.raises(IncidentVersionConflict):
        service.resolve(INCIDENT_ID, expected_version=0, resolution_summary="已恢复", idempotency_key="resolve-1", actor="tester", request_id="req-1")
```

- [x] **Step 2: 运行服务测试并确认接口不存在**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_incident_service.py -q`

Expected: FAIL。

- [x] **Step 3: 实现服务与只读详情投影**

```python
class IncidentDetailView(BaseModel):
    incident: Incident
    rule_name: str
    rule_summary: str
    alerts: tuple[IncidentAlertView, ...]
    activities: tuple[IncidentActivity, ...]
    feishu: FeishuCollaborationView
```

列表默认状态为 `OPEN,ACKNOWLEDGED`，支持状态、环境、严重级别、关键词，`limit<=100`、`offset<=10_000`。详情 Alert 最多 500 条、活动最多 1,000 条，超限时返回 `truncated=true`。

- [x] **Step 4: 编写 API 契约失败测试**

```python
def test_incident_api_lists_real_data_and_supports_state_changes(api_client, auth_headers):
    page = api_client.get("/api/v1/incidents", headers=auth_headers).json()
    assert page["items"][0]["id"] == INCIDENT_ID
    acknowledged = api_client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/acknowledge",
        json={"expected_version": 1},
        headers={**auth_headers, "Idempotency-Key": "ack-1"},
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["incident"]["state"] == "ACKNOWLEDGED"
```

- [x] **Step 5: 实现 schema、路由、依赖接线和错误映射**

`404=incident_not_found`、`409=incident_version_conflict/incident_state_conflict/idempotency_conflict`、`422=validation_error`；响应不得包含数据库任务正文、Secret 或飞书原始事件。

- [x] **Step 6: 运行 API 测试并提交**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_incident_service.py tests/api/test_incidents.py -q`

Expected: PASS。

Commit: `git add backend/src/incident_intelligence/services/incidents.py backend/src/incident_intelligence/api/schemas/incidents.py backend/src/incident_intelligence/api/routes/incidents.py backend/src/incident_intelligence/api/dependencies.py backend/src/incident_intelligence/api/router.py backend/src/incident_intelligence/main.py backend/tests/integration/services/test_incident_service.py backend/tests/api/test_incidents.py && git commit -m "feat: 提供 Incident 运营 API"`

### Task 6：实现飞书路由配置与凭据自检

**Files:**
- Create: `backend/src/incident_intelligence/domain/incident_notifications.py`
- Create: `backend/src/incident_intelligence/services/incident_notification_routes.py`
- Create: `backend/src/incident_intelligence/api/schemas/incident_notification_routes.py`
- Create: `backend/src/incident_intelligence/api/routes/incident_notification_routes.py`
- Create: `backend/migrations/versions/0016_incident_notification_route_operations.py`
- Modify: `backend/src/incident_intelligence/persistence/models.py`
- Modify: `backend/src/incident_intelligence/persistence/incident_repository.py`
- Modify: `backend/src/incident_intelligence/persistence/unit_of_work.py`
- Modify: `backend/src/incident_intelligence/settings.py`
- Modify: `backend/src/incident_intelligence/api/dependencies.py`
- Modify: `backend/src/incident_intelligence/api/router.py`
- Modify: `backend/src/incident_intelligence/main.py`
- Test: `backend/tests/unit/domain/test_incident_notifications.py`
- Test: `backend/tests/api/test_incident_notification_routes.py`

**Interfaces:**
- Produces: `IncidentNotificationRouteService.list/create/update`。
- Produces: `GET/POST /api/v1/incident-notification-routes`、`PATCH /api/v1/incident-notification-routes/{id}`。
- Produces: `FeishuCapabilityView(configured: bool, missing_environment_keys: tuple[str, ...])`，只暴露是否完整，不暴露值。

- [x] **Step 1: 编写领域和配置失败测试**

```python
def test_route_requires_environment_chat_and_version():
    route = IncidentNotificationRoute(environment="production", chat_id="oc_test", chat_name="生产事故群", enabled=True, version=1)
    assert route.enabled_environment_key == "production"


def test_settings_never_render_feishu_secrets(settings_factory):
    settings = settings_factory(feishu_app_secret="secret-value")
    assert "secret-value" not in repr(settings)
    assert "secret-value" not in settings.model_dump_json()
```

- [x] **Step 2: 运行测试并确认模型与配置缺失**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/domain/test_incident_notifications.py tests/api/test_incident_notification_routes.py -q`

Expected: FAIL。

- [x] **Step 3: 实现路由服务和管理 API**

```python
class IncidentNotificationRoute(BaseModel):
    id: str
    environment: Environment
    chat_id: str = Field(min_length=1, max_length=128)
    chat_name: str = Field(min_length=1, max_length=128)
    enabled: bool
    version: int = Field(ge=1)
```

创建、修改使用现有 Bearer Token、幂等键和乐观版本；启用重复环境返回 `409 incident_notification_route_conflict`。应用凭据不完整时仍允许保存停用路由，但启用返回中文缺项码。

- [x] **Step 4: 运行测试并提交**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/domain/test_incident_notifications.py tests/api/test_incident_notification_routes.py -q`

Expected: PASS。

Commit: `git add backend/src/incident_intelligence/domain/incident_notifications.py backend/src/incident_intelligence/services/incident_notification_routes.py backend/src/incident_intelligence/api/schemas/incident_notification_routes.py backend/src/incident_intelligence/api/routes/incident_notification_routes.py backend/src/incident_intelligence/settings.py backend/src/incident_intelligence/api/dependencies.py backend/src/incident_intelligence/api/router.py backend/src/incident_intelligence/main.py backend/tests/unit/domain/test_incident_notifications.py backend/tests/api/test_incident_notification_routes.py && git commit -m "feat: 配置 Incident 飞书通知路由"`

### Task 7：实现 FeishuClient、卡片渲染和通知 Outbox Runner

**Files:**
- Create: `backend/src/incident_intelligence/adapters/feishu.py`
- Create: `backend/src/incident_intelligence/services/incident_notifications.py`
- Create: `backend/src/incident_intelligence/services/incident_notification_runner.py`
- Modify: `backend/src/incident_intelligence/main.py`
- Test: `backend/tests/unit/adapters/test_feishu.py`
- Test: `backend/tests/integration/services/test_incident_notification_service.py`
- Test: `backend/tests/unit/services/test_incident_notification_runner.py`

**Interfaces:**
- Produces: `FeishuClient.get_tenant_token/send_incident_card/update_incident_card/reply_to_thread`。
- Produces: `render_incident_card(IncidentNotificationSnapshot) -> dict[str, object]`。
- Produces: `IncidentNotificationRunner.run_once(limit: int = 20) -> NotificationBatchResult`。

- [x] **Step 1: 编写 HTTP 契约和安全卡片失败测试**

```python
def test_send_card_uses_tenant_token_and_chat_id(fake_transport):
    client = FeishuClient(config(), transport=fake_transport)
    result = client.send_incident_card(chat_id="oc_test", card=card_payload())
    assert result.message_id == "om_root"
    assert fake_transport.requests[-1].headers["Authorization"] == "Bearer tenant-token"


def test_card_contains_incident_facts_but_no_secrets_or_raw_payload():
    card = render_incident_card(snapshot())
    serialized = json.dumps(card, ensure_ascii=False)
    assert "INC-20260901-001" in serialized
    assert "确认事故" in serialized and "解决事故" in serialized
    assert "app_secret" not in serialized and "raw_payload" not in serialized
```

- [x] **Step 2: 运行适配器测试并确认模块不存在**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/adapters/test_feishu.py -q`

Expected: FAIL。

- [x] **Step 3: 实现可注入 HTTP transport 的 FeishuClient**

```python
class FeishuTransport(Protocol):
    def request(self, method: str, url: str, *, headers: dict[str, str], json: dict[str, object], timeout_seconds: float) -> FeishuHttpResponse: ...

class FeishuClient:
    def send_incident_card(self, *, chat_id: str, card: dict[str, object]) -> FeishuMessageResult: ...
    def update_incident_card(self, *, message_id: str, card: dict[str, object]) -> None: ...
    def reply_to_thread(self, *, root_message_id: str, text: str) -> FeishuMessageResult: ...
```

租户 token 只缓存到进程内并在过期前 60 秒刷新；日志只记录 Feishu 错误码和 request_id。HTTP 429、网络错误、5xx 为可重试；鉴权、权限和参数错误为永久失败。

- [x] **Step 4: 编写 Outbox 行为失败测试**

```python
def test_created_notification_sends_root_card_and_binds_thread(service, pending_created):
    service.deliver(pending_created.id)
    binding = get_thread_binding(pending_created.incident_id)
    assert (binding.chat_id, binding.root_message_id) == ("oc_test", "om_root")


def test_alert_link_updates_card_without_thread_spam(service, pending_alert_link):
    service.deliver(pending_alert_link.id)
    assert fake_feishu.updated_message_ids == ["om_root"]
    assert fake_feishu.thread_replies == []


def test_missing_route_completes_without_retry(service, pending_created):
    result = service.deliver(pending_created.id)
    assert result.outcome == "SKIPPED_ROUTE_MISSING"
```

- [x] **Step 5: 实现通知服务、退避与 Runner**

通知键为 `sha256(incident_id|activity_kind|activity_id)`；退避使用 5 秒、30 秒、2 分钟、10 分钟、30 分钟，最多 5 次。永久失败或重试耗尽时写一条 `NOTIFICATION_FAILED`，但该活动不得再次生成失败通知形成循环。

- [x] **Step 6: 运行通知测试并提交**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/adapters/test_feishu.py tests/integration/services/test_incident_notification_service.py tests/unit/services/test_incident_notification_runner.py -q`

Expected: PASS。

Commit: `git add backend/src/incident_intelligence/adapters/feishu.py backend/src/incident_intelligence/services/incident_notifications.py backend/src/incident_intelligence/services/incident_notification_runner.py backend/src/incident_intelligence/main.py backend/tests/unit/adapters/test_feishu.py backend/tests/integration/services/test_incident_notification_service.py backend/tests/unit/services/test_incident_notification_runner.py && git commit -m "feat: 可靠发送 Incident 飞书通知"`

### Task 8：实现飞书事件验签、线程消息回流和卡片动作

**Files:**
- Create: `backend/src/incident_intelligence/services/feishu_events.py`
- Create: `backend/src/incident_intelligence/api/schemas/feishu.py`
- Create: `backend/src/incident_intelligence/api/routes/feishu.py`
- Modify: `backend/src/incident_intelligence/api/dependencies.py`
- Modify: `backend/src/incident_intelligence/api/router.py`
- Modify: `backend/src/incident_intelligence/main.py`
- Test: `backend/tests/unit/services/test_feishu_events.py`
- Test: `backend/tests/api/test_feishu_callbacks.py`

**Interfaces:**
- Produces: `FeishuEventService.handle_event(headers, body) -> FeishuCallbackResult`。
- Produces: `FeishuEventService.handle_card_action(headers, body) -> FeishuCallbackResult`。
- Produces: `POST /api/v1/integrations/feishu/events` 与 `/card-actions`。

- [x] **Step 1: 编写消息过滤和幂等失败测试**

```python
@pytest.mark.parametrize("event", [wrong_chat(), not_thread_reply(), without_bot_mention(), bot_self_message()])
def test_untrusted_or_irrelevant_message_is_ignored(service, event):
    assert service.handle_event(valid_headers(event), event.body).outcome == "IGNORED"
    assert count_feishu_activities() == 0


def test_valid_thread_mention_records_plain_text_once(service):
    event = valid_thread_mention(text="  已联系数据库同学\n等待确认  ")
    first = service.handle_event(valid_headers(event), event.body)
    replay = service.handle_event(valid_headers(event), event.body)
    assert first.outcome == "RECORDED"
    assert replay.outcome == "REPLAYED"
    assert latest_activity().summary == "已联系数据库同学\n等待确认"
```

- [x] **Step 2: 运行服务测试并确认服务不存在**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/services/test_feishu_events.py -q`

Expected: FAIL。

- [x] **Step 3: 实现验证、解密边界和线程回流**

按飞书协议验证时间戳、nonce、签名、Verification Token 和可选 Encrypt Key；时间偏差超过 5 分钟拒绝。只保存规范化 4,000 字纯文本、事件 ID、chat/message/root_message 标识、事件时间和发送者安全摘要；不保存完整请求或附件。

- [x] **Step 4: 编写卡片动作失败测试**

```python
def test_ack_card_action_uses_same_incident_state_machine(service):
    result = service.handle_card_action(valid_headers(), acknowledge_action(expected_version=1))
    assert result.outcome == "ACKNOWLEDGED"
    assert get_incident().state == "ACKNOWLEDGED"


def test_resolve_card_action_requires_resolution_summary(service):
    result = service.handle_card_action(valid_headers(), resolve_action(summary=""))
    assert result.outcome == "VALIDATION_ERROR"
    assert get_incident().state == "OPEN"
```

- [x] **Step 5: 实现回调 API、URL challenge 和错误响应**

回调 API 不接受平台 Bearer Token替代飞书验证。重复事件返回 200；伪造签名返回 401；未知群、未知线程和非文字消息返回 200 且 `outcome=IGNORED`，避免飞书无意义重试。

- [x] **Step 6: 运行回调测试并提交**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/services/test_feishu_events.py tests/api/test_feishu_callbacks.py -q`

Expected: PASS。

Commit: `git add backend/src/incident_intelligence/services/feishu_events.py backend/src/incident_intelligence/api/schemas/feishu.py backend/src/incident_intelligence/api/routes/feishu.py backend/src/incident_intelligence/api/dependencies.py backend/src/incident_intelligence/api/router.py backend/src/incident_intelligence/main.py backend/tests/unit/services/test_feishu_events.py backend/tests/api/test_feishu_callbacks.py && git commit -m "feat: 同步飞书 Incident 协同记录"`

### Task 9：实现 Incident 前端数据层和中文展示投影

**Files:**
- Create: `frontend/src/api/incidents.js`
- Create: `frontend/src/api/incidents.test.js`
- Create: `frontend/src/api/incidentNotificationRoutes.js`
- Create: `frontend/src/api/incidentNotificationRoutes.test.js`
- Create: `frontend/src/presentation/incidentView.js`
- Create: `frontend/src/presentation/incidentView.test.js`
- Create: `frontend/src/composables/useIncidents.js`
- Create: `frontend/src/composables/useIncidents.test.js`

**Interfaces:**
- Produces: `fetchIncidents/fetchIncident/acknowledgeIncident/resolveIncident`。
- Produces: `fetchIncidentNotificationRoutes/createIncidentNotificationRoute/updateIncidentNotificationRoute`。
- Produces: `useIncidents({ autoLoad = true })` 的列表、详情、筛选、状态操作和冲突恢复状态。

- [ ] **Step 1: 编写 API 契约失败测试**

```javascript
it("确认 Incident 时发送版本与幂等键", async () => {
  mockFetchJson({ incident: { id: "inc_1", version: 2 } });
  await acknowledgeIncident("inc_1", 1, "ack-key");
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/incidents/inc_1/acknowledge",
    expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({ "Idempotency-Key": "ack-key" }),
      body: JSON.stringify({ expected_version: 1 }),
    }),
  );
});
```

- [ ] **Step 2: 运行前端目标测试并确认模块不存在**

Run: `cd frontend && npm test -- src/api/incidents.test.js src/api/incidentNotificationRoutes.test.js`

Expected: FAIL。

- [ ] **Step 3: 实现 API 封装和展示投影**

```javascript
export const incidentStateLabel = { OPEN: "待确认", ACKNOWLEDGED: "处理中", RESOLVED: "已解决" };
export const activityKindLabel = {
  INCIDENT_CREATED: "Incident 已创建",
  ALERTS_LINKED: "关联告警已更新",
  SEVERITY_ESCALATED: "严重级别已升级",
  ALL_ALERTS_RECOVERED: "关联告警已全部恢复",
  ACKNOWLEDGED: "Incident 已确认",
  RESOLVED: "Incident 已解决",
  FEISHU_MESSAGE_RECORDED: "飞书沟通已记录",
  NOTIFICATION_FAILED: "飞书通知失败",
};
```

所有错误通过现有 `request.js` 转换为中文可操作信息，不读取技术栈、任务 ID 或原始飞书响应。

- [ ] **Step 4: 编写 composable 并发与错误失败测试**

```javascript
it("版本冲突后保留解决说明并刷新详情", async () => {
  resolveIncident.mockRejectedValueOnce({ code: "incident_version_conflict", userMessage: "Incident 已更新" });
  const state = useIncidents({ autoLoad: false });
  state.resolutionSummary.value = "数据库连接已恢复";
  await state.resolve();
  expect(state.operationState.value).toBe("conflict");
  expect(state.resolutionSummary.value).toBe("数据库连接已恢复");
  expect(fetchIncident).toHaveBeenCalled();
});
```

- [ ] **Step 5: 实现 composable，运行测试并提交**

Run: `cd frontend && npm test -- src/api/incidents.test.js src/api/incidentNotificationRoutes.test.js src/presentation/incidentView.test.js src/composables/useIncidents.test.js`

Expected: PASS。

Commit: `git add frontend/src/api/incidents.js frontend/src/api/incidents.test.js frontend/src/api/incidentNotificationRoutes.js frontend/src/api/incidentNotificationRoutes.test.js frontend/src/presentation/incidentView.js frontend/src/presentation/incidentView.test.js frontend/src/composables/useIncidents.js frontend/src/composables/useIncidents.test.js && git commit -m "feat: 接入 Incident 前端数据层"`

### Task 10：实现 Incident 列表、三栏详情和飞书配置界面

**Files:**
- Create: `frontend/src/components/IncidentCenter.vue`
- Create: `frontend/src/components/IncidentCenter.test.js`
- Create: `frontend/src/components/IncidentDetail.vue`
- Create: `frontend/src/components/IncidentDetail.test.js`
- Create: `frontend/src/components/IncidentNotificationSettings.vue`
- Create: `frontend/src/components/IncidentNotificationSettings.test.js`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/App.test.js`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: Task 9 的 `useIncidents` 与 API。
- Produces: 主导航“Incident 中心”、紧凑列表、详情、确认/解决对话框、飞书路由配置。

- [ ] **Step 1: 编写列表和导航失败测试**

```javascript
it("默认展示未解决 Incident 且列表不出现演示数据", async () => {
  fetchIncidents.mockResolvedValue({ items: [incidentFixture()], total: 1, limit: 50, offset: 0 });
  const wrapper = mount(IncidentCenter);
  await flushPromises();
  expect(wrapper.text()).toContain("INC-20260901-001");
  expect(wrapper.text()).toContain("production checkout异常");
  expect(wrapper.text()).not.toContain("demo");
});
```

- [ ] **Step 2: 运行组件测试并确认组件不存在**

Run: `cd frontend && npm test -- src/components/IncidentCenter.test.js src/components/IncidentDetail.test.js src/components/IncidentNotificationSettings.test.js src/App.test.js`

Expected: FAIL。

- [ ] **Step 3: 实现紧凑列表和筛选**

列表只展示编号/标题、状态、环境/对象、来源规则、Alert 数和最近更新时间；默认过滤 `OPEN,ACKNOWLEDGED`。不增加 KPI 卡、拓扑、AI 结论或自动根因入口；空态明确说明“已发布规则命中新告警后会自动创建 Incident”。

- [ ] **Step 4: 实现三栏详情和状态操作**

左栏“当前情况”，中栏“关联告警”，右栏“处置时间线/飞书协同”；Alert 行复用现有原始告警展开语义。确认按钮直接提交；解决按钮要求 1–2,000 字说明；提交中禁用重复操作，409 冲突后刷新并保留用户输入。

- [ ] **Step 5: 实现飞书配置面板和响应式布局**

按环境展示群名称、chat_id 安全缩略、启停、凭据完整性和最近错误；不得显示 Secret 输入框。宽度低于 1180px 时三栏变为单列分区，任何列表都有页面内滚动，不锁死正文。

- [ ] **Step 6: 运行前端测试、构建并提交**

Run: `cd frontend && npm test && npm run build`

Expected: 全部 PASS，生产构建成功。

Commit: `git add frontend/src/components/IncidentCenter.vue frontend/src/components/IncidentCenter.test.js frontend/src/components/IncidentDetail.vue frontend/src/components/IncidentDetail.test.js frontend/src/components/IncidentNotificationSettings.vue frontend/src/components/IncidentNotificationSettings.test.js frontend/src/App.vue frontend/src/App.test.js frontend/src/styles.css && git commit -m "feat: 交付 Incident 运营界面"`

### Task 11：补齐健康状态、端到端验证、文档和规格归档

**Files:**
- Modify: `backend/src/incident_intelligence/api/routes/health.py`
- Modify: `backend/tests/api/test_health.py`
- Create: `backend/tests/integration/test_alert_to_incident_and_feishu.py`
- Modify: `docs/current-state.md`
- Create: `docs/verification/2026-09-01-incident-operations-and-feishu-collaboration.md`
- Move: `specs/active/incident-operations-and-feishu-collaboration.md` → `specs/completed/incident-operations-and-feishu-collaboration.md`

**Interfaces:**
- Produces: 健康响应中的评估任务、通知任务和飞书配置摘要，不包含任务正文和 Secret。
- Produces: 从 Alert 接收到 Incident 创建、飞书卡片、群聊回流、页面解决的完整可复现验证证据。

- [ ] **Step 1: 编写健康和端到端失败测试**

```python
def test_health_exposes_bounded_worker_status_without_secrets(client, settings):
    body = client.get("/health").json()
    assert body["incident_evaluation"]["failed_count"] == 1
    assert body["feishu"]["configured"] is True
    assert settings.feishu_app_secret.get_secret_value() not in json.dumps(body)


def test_alert_to_incident_feishu_and_resolution_flow(app_harness):
    app_harness.receive_alerts(matching_alertmanager_batch())
    app_harness.run_evaluation_once()
    incident = app_harness.only_incident()
    app_harness.run_notifications_once()
    assert app_harness.feishu.root_cards == [incident.reference]
    app_harness.receive_feishu_message(valid_thread_mention("已开始排查"))
    app_harness.resolve_incident(incident.id, "连接池参数已恢复")
    assert app_harness.get_incident(incident.id).state == "RESOLVED"
```

- [ ] **Step 2: 实现健康摘要并运行完整后端验证**

健康统计每类最多返回总数、最早待处理时间、最近成功时间、最近失败错误码；禁止返回任务 ID 列表、payload、异常堆栈或凭据。

Run: `II_TEST_DATABASE_URL="$II_TEST_DATABASE_URL" ./scripts/verify-backend.sh`

Expected: pytest、覆盖率、ruff、mypy 全部通过。

- [ ] **Step 3: 运行完整前端验证**

Run: `cd frontend && npm test && npm run build`

Expected: 全部测试通过，构建成功。

- [ ] **Step 4: 启动本地服务并完成真实浏览器主流程**

验证顺序：发布规则 → 接收匹配 Alert → 等待 Incident 出现 → 打开详情 → 确认 → 填写解决说明并解决 → 配置页面查看飞书状态。记录页面 URL、测试时间、真实数据来源、每一步结果和截图路径；不得向数据库插入演示 Incident。

- [ ] **Step 5: 使用飞书测试应用和测试群完成沙箱联调**

仅从用户本地环境变量读取凭据，验证：根卡片发送、Alert 追加更新卡片、状态线程消息、`@机器人` 文字回流、重复回调幂等、伪造签名拒绝、飞书临时失败重试。若用户尚未提供测试应用或群，文档明确标记“外部沙箱验证待执行”，不得宣称飞书实测通过，但本地契约测试仍须全部通过。

- [ ] **Step 6: 更新事实文档、验证记录并归档规格**

`docs/current-state.md` 只把已验证能力写成“已实现”；沙箱未联调能力标注“契约已实现，真实飞书待验证”。验证文档记录命令、通过数量、覆盖率、构建结果、浏览器结果、沙箱结果和剩余缺口。

- [ ] **Step 7: 最终提交**

Commit: `git add backend/src/incident_intelligence/api/routes/health.py backend/tests/api/test_health.py backend/tests/integration/test_alert_to_incident_and_feishu.py docs/current-state.md docs/verification/2026-09-01-incident-operations-and-feishu-collaboration.md specs/active/incident-operations-and-feishu-collaboration.md specs/completed/incident-operations-and-feishu-collaboration.md && git commit -m "docs: 完成 Incident 与飞书协同验收"`

## 实施检查点

- 完成 Task 1–2 后：复核领域边界和数据库约束，不进入 API/UI。
- 完成 Task 3–5 后：用真实 Alert 验证 Incident 创建、收敛、恢复事实和人工状态机。
- 完成 Task 6–8 后：先通过假 Feishu transport 和回调契约，再申请真实沙箱联调。
- 完成 Task 9–10 后：按已确认产品图进行浏览器视觉验收，未经确认不增加新功能。
- 完成 Task 11 后：统一验证全部通过，才更新 `docs/current-state.md` 并归档规格。
