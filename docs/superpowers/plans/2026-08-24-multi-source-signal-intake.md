# 多源信号接入与告警投影实施计划

> **供智能执行者：** 必须逐任务使用 `superpowers:executing-plans`；步骤使用复选框跟踪。用户已明确要求只在 `main` 分支开发且不使用子 Agent，因此不得创建工作树、功能分支或派发子 Agent。

**目标：** 实现 Alertmanager Webhook v4 与 CloudEvents 1.0 两个独立认证的生产事件入口，共享一套原子、幂等、可审计的 `SignalEvent → Alert` 接入核心，并保证外部告警不会直接创建 Incident 或 DiagnosisRun。

**架构：** 两个薄适配器只负责协议校验、来源摘要和规范化，统一输出不可变 `SignalCommand`。共享 SignalIntakeService 在 PostgreSQL 单事务中保存 SignalEvent、应用确定性 Alert 投影规则、保存幂等结果和追加有界审计；路径级中间层在 JSON 解析前执行不同容量限制。

**技术栈：** Python 3.13–3.14、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、PostgreSQL 16、Pytest、Ruff、Mypy。

**规格：** `specs/active/multi-source-signal-intake.md`；完整设计见 `docs/superpowers/specs/2026-08-24-multi-source-signal-intake-design.md`。

## 全局约束

- 本阶段只产生或更新 SignalEvent、Alert、signal_intake_results 和审计，不得创建、更新或关闭 Incident、DiagnosisRun。
- `scenario_id`、`scenario_version`、`experiment_id`、注入动作和标准答案在适配前后均递归拒绝。
- 人工报告、Alertmanager、CloudEvents 使用三套互不通用的 Token；Token 不进入源码、日志、响应、测试常量或数据库。
- Alertmanager 请求体上限 262144 字节、每批最多 100 条；CloudEvents 与人工报告上限 65536 字节。
- 不保存原始 Webhook、来源 URI、生成器 URL、请求头、任意查询、未审核 annotations 或 Secret。
- Alertmanager 批次全部预校验后在一个事务中处理；任一步失败整批回滚。
- AI、监控查询、服务目录、事故关联、异步 Worker、Kafka 和前端均不在本计划范围内。
- 每个行为改动必须先看到对应测试因缺少能力而失败，再写最小实现。
- 每个任务提交前运行该任务的聚焦测试；任务 8 执行仓库统一验证与真实 HTTP 冒烟。

---

### 任务 1：扩展领域模型与 PostgreSQL 迁移

**文件：**
- 修改：`backend/src/incident_intelligence/domain/models.py`
- 修改：`backend/src/incident_intelligence/persistence/models.py`
- 修改：`backend/src/incident_intelligence/persistence/repositories.py`
- 修改：`backend/src/incident_intelligence/services/manual_intake.py`
- 修改：`backend/src/incident_intelligence/api/schemas/resources.py`
- 创建：`backend/migrations/versions/0002_multi_source_signal_intake.py`
- 创建：`backend/tests/integration/persistence/test_multi_source_migration.py`
- 修改：`backend/tests/integration/persistence/test_initial_migration.py`
- 修改：`backend/tests/integration/services/test_manual_intake.py`
- 修改：`backend/tests/api/test_resources.py`
- 修改：`backend/tests/unit/domain/test_models.py`

**接口：**
- 产生：`EventType = Literal["manual.reported", "alert.firing", "alert.resolved"]`
- 扩展：`SignalEvent.event_type`
- 扩展：`Alert.source`、`source_instance`、`source_alert_key`、`state_changed_at`
- 产生：`SignalIntakeResultRow`
- 保持：人工报告首次提交与重放的现有 HTTP 契约

- [x] **步骤 1：编写模型和数据库边界失败测试**

在领域测试中断言新字段必填且有界，在迁移测试中先升级到 `0001_initial_domain`、插入一套人工报告记录、再升级到 head 并断言回填：

```python
def test_existing_manual_rows_are_backfilled_without_plain_idempotency_key(
    alembic_config: Config, postgres_engine: Engine
) -> None:
    command.upgrade(alembic_config, "0001_initial_domain")
    insert_legacy_manual_record(postgres_engine, source_event_id="private-key-1")

    command.upgrade(alembic_config, "head")

    with postgres_engine.connect() as connection:
        signal = connection.execute(text("SELECT event_type FROM signal_events")).one()
        alert = connection.execute(
            text(
                "SELECT source, source_instance, source_alert_key, state_changed_at "
                "FROM alerts"
            )
        ).one()
    assert signal.event_type == "manual.reported"
    assert alert.source == "manual"
    assert len(alert.source_instance) == 64
    assert len(alert.source_alert_key) == 64
    assert "private-key-1" not in (alert.source_instance, alert.source_alert_key)
```

同时断言 `(source, source_instance, source_alert_key)` 唯一、event_type 检查约束生效、signal_intake_results 不允许超长 outcome 或指纹。

- [x] **步骤 2：运行失败测试并确认缺少 0002 迁移**

运行：

```bash
cd backend
II_TEST_DATABASE_URL="$II_LOCAL_TEST_DATABASE_URL" .venv/bin/python -m pytest \
  tests/integration/persistence/test_multi_source_migration.py \
  tests/integration/services/test_manual_intake.py \
  tests/api/test_resources.py -q
```

预期：失败原因是新字段、结果表和迁移不存在，而不是测试环境或连接错误。

- [x] **步骤 3：实现领域字段、ORM 与显式迁移**

迁移增加：

```python
op.add_column("signal_events", sa.Column("event_type", sa.String(32), nullable=True))
op.add_column("alerts", sa.Column("source", sa.String(64), nullable=True))
op.add_column("alerts", sa.Column("source_instance", sa.String(64), nullable=True))
op.add_column("alerts", sa.Column("source_alert_key", sa.String(128), nullable=True))
op.add_column("alerts", sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=True))
```

使用 Alembic Python 数据迁移逐行读取现有人工记录，通过 `hashlib.sha256` 计算 `source_instance` 与 `source_alert_key`，然后设为非空并增加：

```python
sa.UniqueConstraint(
    "source", "source_instance", "source_alert_key", name="alert_source_identity"
)
```

创建 `signal_intake_results`，主键为 `(source, source_event_id)`，列为 command_fingerprint、signal_event_id、可空 alert_id、outcome、created_at；只保存 ID 与固定结果码。

- [x] **步骤 4：让人工报告写入新字段并保持读取白名单**

人工报告构造 SignalEvent 与 Alert 时使用：

```python
manual_instance = sha256(b"manual").hexdigest()
manual_alert_key = sha256(idempotency_key.encode("utf-8")).hexdigest()
```

SignalEvent 使用 `event_type="manual.reported"`；Alert 使用 `source="manual"`、上述摘要和 `state_changed_at=now`。读取 API 增加 event_type 与 Alert 新字段，但继续排除 source_event_id、payload_fingerprint 和幂等记录。

- [x] **步骤 5：验证迁移往返和现有能力回归**

运行：

```bash
cd backend
II_DATABASE_URL="$II_LOCAL_TEST_DATABASE_URL" .venv/bin/python -m alembic upgrade head
II_TEST_DATABASE_URL="$II_LOCAL_TEST_DATABASE_URL" .venv/bin/python -m pytest \
  tests/integration/persistence \
  tests/integration/services/test_manual_intake.py \
  tests/api/test_manual_reports.py \
  tests/api/test_resources.py -q
```

预期：迁移、人工报告和读取 API 全部通过；旧幂等键未复制到 Alert 新列。

- [x] **步骤 6：提交领域和迁移**

```bash
git add backend/src/incident_intelligence/domain/models.py \
  backend/src/incident_intelligence/persistence/models.py \
  backend/src/incident_intelligence/persistence/repositories.py \
  backend/src/incident_intelligence/services/manual_intake.py \
  backend/src/incident_intelligence/api/schemas/resources.py \
  backend/migrations/versions/0002_multi_source_signal_intake.py \
  backend/tests/integration/persistence/test_multi_source_migration.py \
  backend/tests/integration/persistence/test_initial_migration.py \
  backend/tests/integration/services/test_manual_intake.py \
  backend/tests/api/test_resources.py \
  backend/tests/unit/domain/test_models.py
git commit -m "feat: extend signal and alert persistence"
```

---

### 任务 2：实现纯领域 Alert 投影决策

**文件：**
- 创建：`backend/src/incident_intelligence/domain/signal_intake.py`
- 创建：`backend/tests/unit/domain/test_signal_intake.py`

**接口：**
- 产生：`SignalCommand`
- 产生：`ProjectionOutcome`，取值 opened、updated、resolved、reopened、stale、orphan_resolved
- 产生：`ProjectionDecision(alert, outcome, reason_code, changes_projection)`
- 产生：`decide_alert_projection(current, command, new_alert_id, signal_event_id, now) -> ProjectionDecision`

- [ ] **步骤 1：写出六种状态行为的失败测试**

使用手工固定 UTC 时间和字面量期望，覆盖首次 firing、ACTIVE 更新、resolved、旧 firing、较新轮次 firing 重开和孤立 resolved：

```python
def test_resolved_alert_reopens_only_for_a_newer_episode() -> None:
    current = resolved_alert(state_changed_at=TIME_2, version=3)
    stale = firing_command(event_at=TIME_1, episode_started_at=TIME_1)
    newer = firing_command(event_at=TIME_3, episode_started_at=TIME_3)

    stale_decision = decide_alert_projection(
        current, stale, "alt_" + "a" * 32, "sig_" + "c" * 32, TIME_4
    )
    reopen_decision = decide_alert_projection(
        current, newer, "alt_" + "b" * 32, "sig_" + "d" * 32, TIME_4
    )

    assert stale_decision.outcome == "stale"
    assert stale_decision.alert is current
    assert reopen_decision.outcome == "reopened"
    assert reopen_decision.alert.state == AlertState.ACTIVE
    assert reopen_decision.alert.version == 4
```

增加同一 event_at 冲突时 resolved 优先、迟到事件不覆盖 signal_event_id、同轮 firing 内容按接收顺序更新但不能倒退 RESOLVED 的测试。

- [ ] **步骤 2：运行测试并确认领域模块不存在**

运行：`cd backend && .venv/bin/python -m pytest tests/unit/domain/test_signal_intake.py -q`

预期：导入失败，明确指向 `domain.signal_intake` 尚未实现。

- [ ] **步骤 3：实现不可变命令与纯决策函数**

`SignalCommand` 使用 Pydantic frozen 模型，限制 source、摘要字段、64 位摘要、最多 20 个 facts 和 normalization_reason_codes。函数不得访问数据库、时钟、UUID 或 HTTP。

实现骨架必须保持纯函数边界：

```python
ProjectionOutcome = Literal[
    "opened", "updated", "resolved", "reopened", "stale", "orphan_resolved"
]


class SignalCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source: Literal["alertmanager", "cloudevents"]
    source_instance: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_alert_key: str = Field(min_length=1, max_length=128)
    event_type: Literal["alert.firing", "alert.resolved"]
    event_at: UtcAwareDatetime
    episode_started_at: UtcAwareDatetime
    title: Title
    summary: Summary
    severity: Severity
    service: ServiceName
    environment: Environment
    facts: dict[FactKey, FactValue] = Field(default_factory=dict, max_length=20)
    normalization_reason_codes: tuple[str, ...] = Field(default=(), max_length=10)
```

决策顺序固定为：孤立 resolved → 事件早于 last_observed → RESOLVED 旧轮 firing → 新轮重开 → ACTIVE resolved → ACTIVE firing 更新。返回新 Alert 时递增版本，stale 与 orphan 不修改现有对象。

- [ ] **步骤 4：运行领域测试并执行变异检查**

运行：`cd backend && .venv/bin/python -m pytest tests/unit/domain/test_signal_intake.py -q`

手工确认以下错误改动会被至少一个测试捕获：把 `>` 改为 `>=`、让 firing 在同时间覆盖 resolved、stale 时更新 signal_event_id、更新时不递增 version。

- [ ] **步骤 5：提交投影规则**

```bash
git add backend/src/incident_intelligence/domain/signal_intake.py \
  backend/tests/unit/domain/test_signal_intake.py
git commit -m "feat: define deterministic alert projection"
```

---

### 任务 3：实现原子幂等的共享信号接入服务

**文件：**
- 修改：`backend/src/incident_intelligence/persistence/repositories.py`
- 修改：`backend/src/incident_intelligence/persistence/unit_of_work.py`
- 创建：`backend/src/incident_intelligence/services/signal_intake.py`
- 创建：`backend/tests/integration/services/test_signal_intake.py`

**接口：**
- 消费：任务 2 的 `SignalCommand` 与 `decide_alert_projection`
- 产生：`SignalIntakeItemResult(signal_event_id, alert_id, outcome, replayed)`
- 产生：`SignalIntakeCounts(opened, updated, resolved, reopened, stale, orphan_resolved, replayed)`
- 产生：`SignalIntakeBatchResult(items, counts)`
- 产生：`SourceEventConflict(reason_code="source_event_conflict")`
- 产生：`SignalIntakeService.submit_batch(commands, actor, request_id) -> SignalIntakeBatchResult`

- [ ] **步骤 1：编写首次接入和禁止创建事故的失败集成测试**

```python
def test_firing_creates_only_signal_alert_result_and_audits(service, session_factory) -> None:
    result = service.submit_batch([firing_command()], "alertmanager", "req-1")

    with session_factory() as session:
        assert count_rows(session, SignalEventRow) == 1
        assert count_rows(session, AlertRow) == 1
        assert count_rows(session, SignalIntakeResultRow) == 1
        assert count_rows(session, AuditEventRow) == 2
        assert count_rows(session, IncidentRow) == 0
        assert count_rows(session, DiagnosisRunRow) == 0
    assert result.items[0].outcome == "opened"
```

- [ ] **步骤 2：运行测试并确认共享服务不存在**

运行：`cd backend && II_TEST_DATABASE_URL="$II_LOCAL_TEST_DATABASE_URL" .venv/bin/python -m pytest tests/integration/services/test_signal_intake.py -q`

预期：因 `SignalIntakeService` 尚未定义而失败。

- [ ] **步骤 3：实现仓储原语和单事务批次**

仓储增加：

```python
def find_signal_result(self, source: str, source_event_id: str) -> SignalIntakeResultRow | None
def find_alert_for_update(
    self, source: str, source_instance: str, source_alert_key: str
) -> AlertRow | None
def update_alert(self, alert: Alert) -> None
def add_signal_result(self, result: SignalIntakeResultRow) -> None
```

`find_alert_for_update` 使用 `SELECT ... FOR UPDATE`。服务先对全部命令执行禁止身份检查和规范化指纹计算，再进入一个工作单元；每个新命令写 SignalEvent、应用投影、写首次结果与固定审计，最后只 commit 一次。

并发唯一约束失败时，服务必须回滚整个批次，在新的工作单元中完整重试一次；重试会读取已提交的 signal_intake_results 并返回重放结果。第二次仍发生 IntegrityError 时原样抛出，交由 API 映射为数据库不可用，不得在已失败事务中继续查询。

服务公开结构固定为：

```python
class SourceEventConflict(Exception):
    reason_code = "source_event_conflict"


class SignalIntakeService:
    def submit_batch(
        self,
        commands: Sequence[SignalCommand],
        actor: str,
        request_id: str,
    ) -> SignalIntakeBatchResult:
        return self._submit_with_single_retry(commands, actor, request_id)
```

- [ ] **步骤 4：增加完整投影、重放和冲突测试**

覆盖 opened、updated、resolved、reopened、stale、orphan_resolved；同一命令重放断言 ID 和首次 alert_id 相同且审计数不变；同一 `(source, source_event_id)` 不同指纹抛出 SourceEventConflict。

```python
def test_orphan_resolved_replay_keeps_original_null_alert_id(service) -> None:
    first = service.submit_batch([orphan_resolved()], "cloudevents", "req-1")
    service.submit_batch([later_firing_same_key()], "cloudevents", "req-2")
    replay = service.submit_batch([orphan_resolved()], "cloudevents", "req-3")

    assert first.items[0].alert_id is None
    assert replay.items[0].alert_id is None
    assert replay.items[0].replayed is True
```

- [ ] **步骤 5：增加批次回滚和并发测试**

两个线程并发提交相同命令，断言只存在一份 SignalEvent、结果记录和首次审计。注入第二条命令写入失败，断言整批 SignalEvent、Alert、结果和审计均为零。批次包含现有重放与新事件时，返回顺序必须与命令顺序一致。

- [ ] **步骤 6：运行共享服务套件**

运行：

```bash
cd backend
II_TEST_DATABASE_URL="$II_LOCAL_TEST_DATABASE_URL" .venv/bin/python -m pytest \
  tests/unit/domain/test_signal_intake.py \
  tests/integration/services/test_signal_intake.py -q
```

预期：状态规则、重放、冲突、并发和全批回滚全部通过。

- [ ] **步骤 7：提交共享接入核心**

```bash
git add backend/src/incident_intelligence/persistence/repositories.py \
  backend/src/incident_intelligence/persistence/unit_of_work.py \
  backend/src/incident_intelligence/services/signal_intake.py \
  backend/tests/integration/services/test_signal_intake.py
git commit -m "feat: add shared signal intake service"
```

---

### 任务 4：实现 Alertmanager v4 纯适配器

**文件：**
- 创建：`backend/src/incident_intelligence/adapters/__init__.py`
- 创建：`backend/src/incident_intelligence/adapters/common.py`
- 创建：`backend/src/incident_intelligence/adapters/alertmanager.py`
- 创建：`backend/tests/unit/adapters/test_alertmanager.py`

**接口：**
- 产生：`normalize_source_uri(value: str) -> str`
- 产生：`AlertmanagerWebhook` 与有界嵌套模型
- 产生：`to_signal_commands(webhook, now) -> tuple[SignalCommand, ...]`
- 产生：`AdapterValidationError(reason_code)`

- [ ] **步骤 1：编写官方形状转换失败测试**

固定 Webhook 含 groupKey、receiver、status、externalURL、commonLabels、alerts，并断言输出字面量：

```python
def test_alertmanager_firing_maps_to_bounded_signal_command() -> None:
    command = to_signal_commands(AlertmanagerWebhook.model_validate(FIRING_PAYLOAD), NOW)[0]

    assert command.source == "alertmanager"
    assert command.source_alert_key == "fingerprint-1"
    assert command.event_type == "alert.firing"
    assert command.title == "支付接口错误率升高"
    assert command.severity == "high"
    assert command.service == "payment-api"
    assert command.environment == "production"
    assert len(command.source_instance) == 64
    assert "generatorURL" not in command.model_dump_json()
```

- [ ] **步骤 2：运行测试并确认适配器不存在**

运行：`cd backend && .venv/bin/python -m pytest tests/unit/adapters/test_alertmanager.py -q`

预期：因 adapters.alertmanager 不存在而失败。

- [ ] **步骤 3：实现 URI 规范化、字段模型和摘要**

URI 规范化拒绝无 scheme/host，去除 userinfo、query、fragment，保留小写 scheme/host、显式端口和规范 path。只把规范 URI送入 SHA-256，不把它放入命令。

```python
def normalize_source_uri(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or parsed.username is not None or parsed.password is not None:
        raise AdapterValidationError("invalid_source_uri")
    hostname = parsed.hostname.casefold() if parsed.hostname is not None else ""
    if parsed.netloc and not hostname:
        raise AdapterValidationError("invalid_source_uri")
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = f":{parsed.port}" if parsed.port is not None else ""
    authority = f"{host}{port}" if parsed.netloc else ""
    return urlunsplit((parsed.scheme.casefold(), authority, parsed.path, "", ""))


def to_signal_commands(
    webhook: AlertmanagerWebhook,
    now: datetime,
) -> tuple[SignalCommand, ...]:
    return tuple(to_signal_command(webhook.external_url, alert, now) for alert in webhook.alerts)
```

标签输入最多 100 项、annotation 最多 20 项；facts 白名单固定为 region、cluster、namespace、pod、instance、job、team、component、node、container，最终最多 20 项。

- [ ] **步骤 4：实现严重度和时间映射并增加边界测试**

测试所有固定严重度别名、未知严重度的 medium + severity_defaulted、resolved 必须 endsAt、未来五分钟限制、缺失 fingerprint/service/title、禁止身份任意深度、JSON 键序和分组字段变化不改变 source_event_id、真实规范化内容变化会改变 source_event_id。

- [ ] **步骤 5：运行适配器测试**

运行：`cd backend && .venv/bin/python -m pytest tests/unit/adapters/test_alertmanager.py -q`

预期：转换、容量、脱敏、禁止身份与确定性摘要测试全部通过。

- [ ] **步骤 6：提交 Alertmanager 适配器**

```bash
git add backend/src/incident_intelligence/adapters \
  backend/tests/unit/adapters/test_alertmanager.py
git commit -m "feat: normalize alertmanager webhooks"
```

---

### 任务 5：接入独立认证、路径容量与 Alertmanager HTTP API

**文件：**
- 修改：`backend/src/incident_intelligence/settings.py`
- 修改：`backend/src/incident_intelligence/api/dependencies.py`
- 修改：`backend/src/incident_intelligence/api/middleware.py`
- 创建：`backend/src/incident_intelligence/api/schemas/intake.py`
- 创建：`backend/src/incident_intelligence/api/routes/alertmanager.py`
- 修改：`backend/src/incident_intelligence/api/router.py`
- 修改：`backend/src/incident_intelligence/main.py`
- 修改：`backend/tests/conftest.py`
- 创建：`backend/tests/api/test_alertmanager_intake.py`
- 修改：`backend/tests/api/test_manual_reports.py`
- 修改：`backend/tests/api/test_resources.py`

**接口：**
- 产生：`POST /api/v1/intake/alertmanager`
- 产生：`require_manual_actor`、`require_alertmanager_actor`、`require_cloudevents_actor`
- 产生：`IntakeItemResponse` 与 `IntakeBatchResponse`
- 修改：RequestBodyLimitMiddleware 支持按精确路径选择上限

- [ ] **步骤 1：编写认证隔离与容量失败测试**

动态生成三套 Token，不把固定凭据写入测试：

```python
def test_alertmanager_rejects_manual_and_cloudevents_tokens(api_tokens, client, payload) -> None:
    for token in (api_tokens.manual, api_tokens.cloudevents):
        response = client.post(
            "/api/v1/intake/alertmanager",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_required"
```

增加 262145 字节返回 413、101 条返回 422 batch_too_large、人工报告仍在 65536 字节拒绝的测试。

- [ ] **步骤 2：运行 API 测试并确认路由 404**

运行：`cd backend && II_TEST_DATABASE_URL="$II_LOCAL_TEST_DATABASE_URL" .venv/bin/python -m pytest tests/api/test_alertmanager_intake.py -q`

预期：合法调用为 404，证明路由尚未注册。

- [ ] **步骤 3：扩展 Settings 和认证依赖**

Settings 增加：

```python
alertmanager_token: SecretStr
cloudevents_token: SecretStr
alertmanager_body_limit_bytes: int = Field(default=262_144, ge=65_536, le=1_048_576)
cloudevents_body_limit_bytes: int = Field(default=65_536, gt=0, le=262_144)
```

抽取内部 `_require_token(credentials, expected, actor)`，三套公开依赖分别读取对应 SecretStr 并使用 bytes compare_digest。现有人工报告和资源读取改用 require_manual_actor。

- [ ] **步骤 4：实现路径级容量中间层**

中间层构造参数改为默认上限与精确路径字典：

```python
RequestBodyLimitMiddleware(
    app,
    default_max_bytes=settings.request_body_limit_bytes,
    path_limits={
        "/api/v1/intake/alertmanager": settings.alertmanager_body_limit_bytes,
        "/api/v1/intake/cloudevents": settings.cloudevents_body_limit_bytes,
    },
)
```

声明 Content-Length 和无长度分块累计都使用同一路径上限，413 响应只含固定 code/message。

- [ ] **步骤 5：实现 Alertmanager 路由与响应**

路由先由 Pydantic 完整验证 AlertmanagerWebhook，再调用 to_signal_commands 和 `app.state.signal_intake_service.submit_batch`。首次成功返回 202；完全重放仍返回 200。响应按输入顺序返回 ID/outcome/replayed，并产生固定计数。

```python
@router.post("/api/v1/intake/alertmanager", response_model=IntakeBatchResponse)
def receive_alertmanager(
    webhook: AlertmanagerWebhook,
    response: Response,
    actor: Annotated[str, Depends(require_alertmanager_actor)],
    service: Annotated[SignalIntakeService, Depends(get_signal_intake_service)],
) -> IntakeBatchResponse:
    result = service.submit_batch(
        to_signal_commands(webhook, datetime.now(UTC)),
        actor,
        f"req_{uuid4().hex}",
    )
    response.status_code = 200 if all(item.replayed for item in result.items) else 202
    return IntakeBatchResponse.from_result(result)
```

适配器错误映射 validation_error 或 forbidden_identity；SourceEventConflict 映射 409；SQLAlchemyError 继续映射 503。

- [ ] **步骤 6：验证原子批次、安全响应和零事故**

API 测试覆盖合法单条、多条、重放、更新、恢复、批次一条非法全量零写入、数据库故障全量回滚、日志不含正文或 Token，并断言 IncidentRow 与 DiagnosisRunRow 数量为零。

- [ ] **步骤 7：运行 Alertmanager 与现有 API 回归**

运行：

```bash
cd backend
II_TEST_DATABASE_URL="$II_LOCAL_TEST_DATABASE_URL" .venv/bin/python -m pytest \
  tests/api/test_alertmanager_intake.py \
  tests/api/test_manual_reports.py \
  tests/api/test_resources.py -q
```

预期：三套 Token 隔离、两种容量和现有接口全部通过。

- [ ] **步骤 8：提交 Alertmanager HTTP 接入**

```bash
git add backend/src/incident_intelligence/settings.py \
  backend/src/incident_intelligence/api/dependencies.py \
  backend/src/incident_intelligence/api/middleware.py \
  backend/src/incident_intelligence/api/schemas/intake.py \
  backend/src/incident_intelligence/api/routes/alertmanager.py \
  backend/src/incident_intelligence/api/router.py \
  backend/src/incident_intelligence/main.py \
  backend/tests/conftest.py \
  backend/tests/api/test_alertmanager_intake.py \
  backend/tests/api/test_manual_reports.py \
  backend/tests/api/test_resources.py
git commit -m "feat: accept secured alertmanager webhooks"
```

---

### 任务 6：实现 CloudEvents 1.0 纯适配器

**文件：**
- 创建：`backend/src/incident_intelligence/adapters/cloudevents.py`
- 创建：`backend/tests/unit/adapters/test_cloudevents.py`

**接口：**
- 产生：`CloudEventData`、`StructuredCloudEvent`、`BinaryCloudEventContext`
- 产生：`structured_to_signal_command(event, now) -> SignalCommand`
- 产生：`binary_to_signal_command(context, data, now) -> SignalCommand`

- [ ] **步骤 1：编写结构化与 Binary 等价失败测试**

```python
def test_structured_and_binary_modes_produce_the_same_command() -> None:
    structured = structured_to_signal_command(STRUCTURED_EVENT, NOW)
    binary = binary_to_signal_command(BINARY_CONTEXT, EVENT_DATA, NOW)

    assert structured == binary
    assert structured.source == "cloudevents"
    assert structured.source_alert_key == "payment-error-rate"
    assert len(structured.source_event_id) == 64
```

- [ ] **步骤 2：运行测试并确认 CloudEvents 适配器不存在**

运行：`cd backend && .venv/bin/python -m pytest tests/unit/adapters/test_cloudevents.py -q`

预期：因 adapters.cloudevents 不存在而失败。

- [ ] **步骤 3：实现严格上下文与 data 模型**

只接受 specversion 1.0、type `com.incidentintelligence.alert.v1`；结构化标准字段限定 specversion、id、source、type、subject、time、datacontenttype、data，其他扩展属性拒绝。Binary 必需 ce-specversion/id/source/type/time，ce-subject 可选，Content-Type 必须 JSON。

data 使用 extra=forbid，严格限制 alert_key、title、summary、severity、service、environment、status、started_at、最多 20 个 labels。

```python
class CloudEventData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alert_key: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    title: Title
    summary: Summary
    severity: Severity
    service: ServiceName
    environment: Environment
    status: Literal["firing", "resolved"]
    started_at: AwareDatetime
    labels: dict[LabelKey, LabelValue] = Field(default_factory=dict, max_length=20)


def structured_to_signal_command(
    event: StructuredCloudEvent,
    now: datetime,
) -> SignalCommand:
    return cloud_event_to_command(event.context(), event.data, now)
```

- [ ] **步骤 4：实现 URI 摘要、身份和一致性校验**

复用 normalize_source_uri。source_instance 是规范 URI摘要；source_event_id 是 `SHA-256(normalized_source + b"\0" + id)`。subject 存在时必须等于 service；time 与 started_at 转 UTC，time 不得超过 now 五分钟。

- [ ] **步骤 5：增加冲突与安全边界测试**

覆盖不同 source 相同 id 不碰撞、同 source/id 不同 data 生成相同 source_event_id 供服务检测冲突、不支持 type、扩展属性、subject 不一致、未来时间、禁止身份、原始 source URI 不进入命令 JSON。

- [ ] **步骤 6：运行适配器测试并提交**

运行：`cd backend && .venv/bin/python -m pytest tests/unit/adapters/test_cloudevents.py -q`

```bash
git add backend/src/incident_intelligence/adapters/cloudevents.py \
  backend/tests/unit/adapters/test_cloudevents.py
git commit -m "feat: normalize cloudevents alerts"
```

---

### 任务 7：暴露 CloudEvents HTTP API 并验证跨来源隔离

**文件：**
- 创建：`backend/src/incident_intelligence/api/routes/cloudevents.py`
- 修改：`backend/src/incident_intelligence/api/router.py`
- 修改：`backend/src/incident_intelligence/api/errors.py`
- 创建：`backend/tests/api/test_cloudevents_intake.py`
- 创建：`backend/tests/integration/services/test_source_isolation.py`

**接口：**
- 产生：`POST /api/v1/intake/cloudevents`
- 消费：任务 5 的独立认证、路径容量和共享响应模型
- 消费：任务 6 的 structured/binary 转换函数

- [ ] **步骤 1：编写两种内容模式的失败 API 测试**

结构化请求使用 `application/cloudevents+json`；Binary 请求使用 ce-* 头与 JSON data。两者使用不同 id 但相同 alert_key，断言第二次更新同一 Alert 而不是创建第二条：

```python
def test_structured_and_binary_events_update_one_alert(client, cloud_headers) -> None:
    first = client.post(CLOUD_PATH, headers=structured_headers(cloud_headers), json=STRUCTURED)
    second = client.post(CLOUD_PATH, headers=binary_headers(cloud_headers), json=BINARY_DATA)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["items"][0]["alert_id"] == second.json()["items"][0]["alert_id"]
```

- [ ] **步骤 2：运行测试并确认 CloudEvents 路由 404**

运行：`cd backend && II_TEST_DATABASE_URL="$II_LOCAL_TEST_DATABASE_URL" .venv/bin/python -m pytest tests/api/test_cloudevents_intake.py -q`

预期：合法请求返回 404，证明路由尚未注册。

- [ ] **步骤 3：实现内容模式分派与安全错误映射**

路由按精确 Content-Type 选择结构化或 Binary；不支持内容类型返回 415 `unsupported_media_type`。缺失 ce-*、不支持事件类型、Pydantic 错误和禁止身份分别映射稳定错误，不回显 header 或 data。

```python
@router.post("/api/v1/intake/cloudevents", response_model=IntakeBatchResponse)
async def receive_cloudevent(
    request: Request,
    response: Response,
    actor: Annotated[str, Depends(require_cloudevents_actor)],
    service: Annotated[SignalIntakeService, Depends(get_signal_intake_service)],
) -> IntakeBatchResponse:
    content_type = request.headers.get("content-type", "").partition(";")[0].casefold()
    payload = await request.json()
    if content_type == "application/cloudevents+json":
        event = StructuredCloudEvent.model_validate(payload)
        command = structured_to_signal_command(event, datetime.now(UTC))
    elif content_type == "application/json":
        context = BinaryCloudEventContext.from_headers(request.headers)
        data = CloudEventData.model_validate(payload)
        command = binary_to_signal_command(context, data, datetime.now(UTC))
    else:
        raise ApiError(415, "unsupported_media_type", "不支持该事件内容类型")
    result = service.submit_batch((command,), actor, f"req_{uuid4().hex}")
    response.status_code = 200 if result.items[0].replayed else 202
    return IntakeBatchResponse.from_result(result)
```

- [ ] **步骤 4：验证幂等、冲突、乱序和容量**

测试 `(source,id)` 完全重复返回 200 replay；同 identity 不同 data 返回 409 source_event_conflict；resolved 后旧 firing 只增加 SignalEvent，不回退 Alert；超过 65536 字节在 JSON 解析前返回 413。

- [ ] **步骤 5：验证来源与凭据隔离**

集成测试使用相同 alert_key：两个 CloudEvents source 形成两个 Alert；Alertmanager fingerprint 与 CloudEvents alert_key 相同仍形成不同 Alert。人工、Alertmanager、CloudEvents 三个 Token 对非所属入口均返回相同 401。

- [ ] **步骤 6：验证不创建事故且不泄露输入**

提交 firing、resolved、迟到和冲突后，IncidentRow 与 DiagnosisRunRow 仍为零。扫描 API 响应、caplog、SignalEvent facts、AuditEvent details 和 signal_intake_results，确认 source URI、Token、完整标题外的未审核字段、查询和生成器 URL均不存在。

- [ ] **步骤 7：运行多源 API 与服务套件**

运行：

```bash
cd backend
II_TEST_DATABASE_URL="$II_LOCAL_TEST_DATABASE_URL" .venv/bin/python -m pytest \
  tests/unit/adapters \
  tests/integration/services/test_signal_intake.py \
  tests/integration/services/test_source_isolation.py \
  tests/api/test_alertmanager_intake.py \
  tests/api/test_cloudevents_intake.py -q
```

预期：两种协议模式、三套认证、来源隔离和零事故边界全部通过。

- [ ] **步骤 8：提交 CloudEvents HTTP 接入**

```bash
git add backend/src/incident_intelligence/api/routes/cloudevents.py \
  backend/src/incident_intelligence/api/router.py \
  backend/src/incident_intelligence/api/errors.py \
  backend/tests/api/test_cloudevents_intake.py \
  backend/tests/integration/services/test_source_isolation.py
git commit -m "feat: accept secured cloudevents alerts"
```

---

### 任务 8：全量验证、真实冒烟与诚实状态更新

**文件：**
- 修改：`README.md`
- 修改：`docs/current-state.md`
- 修改：`docs/architecture.md`
- 修改：`specs/active/multi-source-signal-intake.md`
- 创建：`docs/verification/2026-08-24-multi-source-signal-intake.md`

**接口：**
- 产生：可重复的多源接入启动、配置和验证说明
- 产生：只记录已实现验收项的阶段证据

- [ ] **步骤 1：在 Compose 独立数据库运行统一验证**

使用只存在于当前终端的随机数据库密码和三套随机 Token：

```bash
docker compose up -d --wait postgres
II_TEST_DATABASE_URL="$II_LOCAL_TEST_DATABASE_URL" scripts/verify-backend.sh
```

预期：Ruff、格式、Mypy、迁移升级/降级、全部测试和覆盖率 90% 门槛零失败。

- [ ] **步骤 2：执行真实 Alertmanager HTTP 冒烟**

迁移数据库并启动 Uvicorn，提交 firing 批次、完全重放、内容更新、resolved 和旧 firing。只记录状态码、资源 ID 是否一致、Alert 最终状态和 Incident/DiagnosisRun 数量，不记录 Token、完整正文或来源 URI。

- [ ] **步骤 3：执行两种 CloudEvents HTTP 冒烟**

使用结构化模式创建 ACTIVE，再用 Binary 模式更新同一 alert_key，随后 resolved。验证 source + id 重放、两个模式指向同一 Alert、读取接口返回 RESOLVED，且外部事件未创建事故或诊断任务。

- [ ] **步骤 4：执行安全与禁止内容扫描**

扫描仓库和验收数据库：Secret 形态无命中；实验身份只存在于拒绝实现、边界测试和说明文档；数据库列、响应样本和审计中没有原始 URI、生成器 URL、查询或原始负载。

- [ ] **步骤 5：更新说明与实际状态**

README 记录三套 Token、两个入口、容量和最小示例但使用占位符。current-state 与 architecture 只把 SignalEvent/Alert 外部接入标为已实现；服务目录、关联、Incident 自动创建、取证、Worker、AI、事故运营和前端继续标为未实现。

- [ ] **步骤 6：记录验收证据并移动规格**

验收文档记录命令、测试数量、覆盖率、迁移版本、HTTP 冒烟结论和已知缺口，不保存任何 Secret 或完整外部事件。全部验收通过后把：

```text
specs/active/multi-source-signal-intake.md
```

移动为：

```text
specs/completed/multi-source-signal-intake.md
```

并把状态改为“已验收”。

- [ ] **步骤 7：提交验收记录**

```bash
git add README.md docs specs
git commit -m "docs: verify multi-source signal intake"
```

- [ ] **步骤 8：清理临时资源并确认仓库状态**

停止临时 Uvicorn，删除本轮 Compose 容器、网络、专用测试卷和临时日志，清除终端中的数据库密码与三套 Token。运行：

```bash
git status --short --branch
git log --oneline -10
```

预期：位于 main、工作区干净、提交记录按任务排列；不得存在额外分支或工作树。

## 计划自检

- **规格覆盖：** 任务 1 覆盖领域与迁移；任务 2 覆盖确定性投影；任务 3 覆盖共享事务、幂等、并发和审计；任务 4–5 覆盖 Alertmanager；任务 6–7 覆盖 CloudEvents 两种模式与来源隔离；任务 8 覆盖全量、安全和真实 HTTP 验收。
- **边界覆盖：** 每个外部入口只写 SignalEvent、Alert、signal_intake_results 和审计；所有服务与 API 测试都断言 Incident、DiagnosisRun 不变。
- **安全覆盖：** 三套独立 Token、路径级容量、双重禁止身份检查、来源 URI 摘要、原始负载不落盘和安全错误均有明确测试。
- **类型一致：** SignalCommand、ProjectionDecision、SignalIntakeItemResult 和两个适配器函数在首次产生任务中定义，后续任务使用相同名称。
- **无占位实现：** 每个任务给出精确文件、接口、失败原因、聚焦命令、关键实现规则和提交边界。
