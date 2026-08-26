# 告警中心与告警源管理实施计划

> **执行要求：** 必须使用 `superpowers:executing-plans` 按任务顺序执行，并用复选框跟踪进度。本仓库明确禁止子 Agent、Git worktree 和功能分支，所有变更直接提交到 `main`。

**目标：** 在保留现有三类接入、Alert 投影和事故关联链路的前提下，实现独立告警源、独立凭据、真实接收记录、告警中心和告警源管理页面。

**架构：** 来源注册表位于现有 Alertmanager/CloudEvents 纯适配器之前，认证后向 `SignalCommand` 注入可信 `alert_source_id`。普通接入在 MySQL 单事务中保存来源 Receipt、SignalEvent、Alert、接入结果、审计和必要的关联任务；前端通过新增有界读写 API 展示真实数据，不使用演示数据。

**技术栈：** Python 3.13、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、MySQL 8.4、Pytest、Vue 3、Vite 6、Vitest、Playwright/浏览器人工验收。前端沿用当前 JavaScript composable 结构，本阶段不额外引入路由器、Pinia 或 TypeScript 迁移。

**规格：** `docs/superpowers/specs/2026-08-26-alert-center-source-management-design.md`

## 全局约束

- 只在 `main` 分支顺序开发，不创建分支、worktree 或子 Agent。
- 行为改动一律先写失败测试，再写最小完整实现。
- 主数据库固定为 MySQL 8.4、InnoDB、utf8mb4、READ COMMITTED 和 UTC `DATETIME(6)`。
- Alertmanager 请求体最多 256 KiB、单批最多 100 条；CloudEvents 请求体最多 64 KiB。
- Token 至少包含 256 位随机秘密，数据库只保存 SHA-256 摘要，明文只在首次成功响应中展示。
- `scenario_id`、`scenario_version`、`experiment_id`、注入动作和标准答案不得进入新模型、Receipt、API、日志、测试数据或页面。
- 不保存原始 Webhook 请求、请求头、外部敏感 URL、明文 Token 或异常堆栈。
- 每个来源物理 Receipt 最多 1,000 条；读取仅返回最近 30 天且单页最多 100 条。
- 告警中心只读，不新增告警确认、关闭、抑制、静默或另一套人工状态机。
- API 或关联能力失败不能阻断已有告警读取和人工事故处置，前端不得回退到演示数据。

---

### 任务 1：来源身份模型与 MySQL 迁移

**文件：**

- 新建：`backend/migrations/versions/0005_alert_sources.py`
- 新建：`backend/src/incident_intelligence/domain/alert_sources.py`
- 修改：`backend/src/incident_intelligence/domain/models.py`
- 修改：`backend/src/incident_intelligence/domain/signal_intake.py`
- 修改：`backend/src/incident_intelligence/ids.py`
- 修改：`backend/src/incident_intelligence/persistence/models.py`
- 修改：`backend/src/incident_intelligence/persistence/repositories.py`
- 测试：`backend/tests/unit/domain/test_alert_sources.py`
- 测试：`backend/tests/unit/domain/test_models.py`
- 测试：`backend/tests/unit/domain/test_signal_intake.py`
- 测试：`backend/tests/integration/persistence/test_alert_source_migration.py`
- 测试：`backend/tests/integration/persistence/test_constraints.py`

**接口：**

- 产出 `AlertSourceType`、`AlertSourceManagementType`、`AlertSourceState`、`CredentialState` 和 `ReceiptOutcome` 字面值类型。
- 产出系统来源常量：`MANUAL_SYSTEM_SOURCE_ID = "src_00000000000000000000000000000001"`、`ALERTMANAGER_COMPAT_SOURCE_ID = "src_00000000000000000000000000000002"`、`CLOUDEVENTS_COMPAT_SOURCE_ID = "src_00000000000000000000000000000003"`。
- `SignalCommand.alert_source_id: str`、`SignalEvent.alert_source_id: str`、`Alert.alert_source_id: str` 均使用 `^src_[0-9a-f]{32}$`。
- `RecordRepositories.get_intake_result()`、`get_alert()` 和唯一身份查询全部接收 `alert_source_id`。

- [x] **步骤 1：先写领域和迁移失败测试**

```python
def test_signal_command_requires_trusted_alert_source_id() -> None:
    with pytest.raises(ValidationError):
        firing_command(alert_source_id="external-payload-value")


def test_migration_backfills_system_sources(mysql_database_url: str) -> None:
    upgrade_to("0004_incident_lifecycle", mysql_database_url)
    seed_manual_alertmanager_and_cloudevents_rows(mysql_database_url)
    upgrade_to("0005_alert_sources", mysql_database_url)
    assert_source_ids_are_non_null_and_match_expected_system_sources(mysql_database_url)
```

- [x] **步骤 2：运行聚焦测试确认失败**

运行：

```bash
cd backend
.venv/bin/python -m pytest tests/unit/domain/test_alert_sources.py tests/unit/domain/test_models.py tests/unit/domain/test_signal_intake.py tests/integration/persistence/test_alert_source_migration.py -q
```

预期：因来源类型、`alert_source_id` 字段和 `0005_alert_sources` 迁移尚不存在而失败。

- [x] **步骤 3：实现领域类型和 ORM 字段**

```python
AlertSourceType = Literal["ALERTMANAGER", "CLOUDEVENTS", "MANUAL"]
AlertSourceManagementType = Literal["USER_MANAGED", "SYSTEM_MANAGED"]
AlertSourceState = Literal["ENABLED", "DISABLED"]
CredentialState = Literal["ACTIVE", "REVOKED"]
ReceiptOutcome = Literal[
    "ACCEPTED", "REPLAYED", "VALIDATED", "PAYLOAD_REJECTED",
    "SOURCE_DISABLED", "PROCESSING_FAILED",
]

class SignalCommand(BaseModel):
    alert_source_id: str = Field(pattern=r"^src_[0-9a-f]{32}$")
```

同时新增 `AlertSourceRow`、`AlertSourceCredentialRow`、`AlertSourceReceiptRow` 和 `AlertSourceOperationRow`；为 `signal_events`、`alerts`、`signal_intake_results`、`correlation_jobs` 增加非空来源外键，并把事件与告警唯一约束改为包含 `alert_source_id`。

- [x] **步骤 4：实现可升级和可降级迁移**

迁移必须先创建三个确定性系统来源，再按原 `source` 回填现有领域数据，最后增加非空和外键约束。降级先恢复旧唯一约束，再删除新增列和四张来源表。测试必须证明升级、降级和 ORM 元数据一致。

- [x] **步骤 5：更新现有测试工厂和仓储签名**

所有 `SignalCommand` 测试工厂显式使用系统或测试来源 ID；仓储查询示例：

```python
def get_alert(
    self,
    alert_source_id: str,
    source: str,
    source_instance: str,
    source_alert_key: str,
) -> AlertRow | None:
    ...
```

- [x] **步骤 6：运行迁移和领域回归**

运行：

```bash
cd backend
.venv/bin/python -m pytest tests/unit/domain tests/integration/persistence -q
```

预期：全部通过，已有三类数据被确定性回填且跨来源身份不冲突。

- [ ] **步骤 7：提交任务 1**

```bash
git add backend/migrations/versions/0005_alert_sources.py backend/src/incident_intelligence/domain backend/src/incident_intelligence/persistence backend/tests
git commit -m "feat: 建立告警源身份模型"
```

---

### 任务 2：独立凭据生命周期与告警源管理 API

**文件：**

- 新建：`backend/src/incident_intelligence/persistence/alert_source_repository.py`
- 新建：`backend/src/incident_intelligence/services/alert_sources.py`
- 新建：`backend/src/incident_intelligence/api/schemas/alert_sources.py`
- 新建：`backend/src/incident_intelligence/api/routes/alert_sources.py`
- 修改：`backend/src/incident_intelligence/api/dependencies.py`
- 修改：`backend/src/incident_intelligence/api/router.py`
- 修改：`backend/src/incident_intelligence/main.py`
- 测试：`backend/tests/integration/services/test_alert_source_service.py`
- 测试：`backend/tests/api/test_alert_sources.py`

**接口：**

- `AlertSourceService.create_source(command, idempotency_key, actor, request_id, now) -> AlertSourceMutationResult`。
- `AlertSourceService.update_source(source_id, command, ...) -> AlertSourceMutationResult`。
- `AlertSourceService.rotate_credential(source_id, expected_version, ...) -> CredentialMutationResult`。
- `AlertSourceService.revoke_credential(source_id, credential_id, expected_version, ...) -> AlertSourceMutationResult`。
- `AlertSourceService.list_sources(filters) -> AlertSourcePage`、`get_source(source_id) -> AlertSourceOverview`、`list_receipts(source_id, limit, offset, now) -> ReceiptPage`。
- 一次性 Token 形态为 `iisrc_<credential_id>.<secret>`；`credential_id` 为 `acr_` 加 32 位十六进制，`secret` 由 `secrets.token_urlsafe(32)` 生成。

- [ ] **步骤 1：写服务失败测试**

```python
def test_create_source_returns_secret_once_and_persists_only_digest(service) -> None:
    first = service.create_source(create_command(), "request-1", ACTOR, REQUEST_ID, NOW)
    replay = service.create_source(create_command(), "request-1", ACTOR, REQUEST_ID, NOW)
    assert first.secret_retrievable is True
    assert first.token.startswith(f"iisrc_{first.credential_id}.")
    assert replay.secret_retrievable is False
    assert replay.token is None
    assert first.token not in dump_all_persisted_text()


def test_enabled_source_cannot_revoke_last_active_credential(service) -> None:
    source = create_user_source(service)
    with pytest.raises(LastActiveCredentialError):
        service.revoke_credential(source.id, source.credential_id, source.version, ...)
```

- [ ] **步骤 2：运行服务测试确认失败**

运行：

```bash
cd backend
.venv/bin/python -m pytest tests/integration/services/test_alert_source_service.py -q
```

预期：因服务、仓储和凭据生成逻辑不存在而失败。

- [ ] **步骤 3：实现凭据与幂等服务**

```python
def issue_token(credential_id: str) -> tuple[str, str]:
    secret = token_urlsafe(32)
    token = f"iisrc_{credential_id}.{secret}"
    return token, sha256(token.encode("utf-8")).hexdigest()
```

创建、修改、轮换和撤销均使用行锁、乐观版本和摘要化幂等操作。幂等记录只保存固定动作、命令指纹、结果来源 ID、结果凭据 ID、结果版本和 `secret_retrievable=False` 的重放事实。

- [ ] **步骤 4：写管理 API 失败测试**

覆盖创建 201、精确重放 200 且不返回 Token、名称冲突 409、旧版本 409、系统来源只读、停用/启用、轮换、撤销和最后凭据保护。响应检查不得出现 `token_digest`、actor、请求正文或内部异常。

- [ ] **步骤 5：实现 Schema、路由和依赖注入**

```python
@router.post("", response_model=AlertSourceMutationResponse, status_code=201)
def create_alert_source(
    command: AlertSourceCreateRequest,
    actor: Annotated[str, Depends(require_manual_actor)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[AlertSourceService, Depends(get_alert_source_service)],
) -> AlertSourceMutationResponse:
    ...
```

列表 `limit` 默认 50、最大 100；详情只返回凭据 ID、状态、创建/最后使用/撤销时间，永不返回摘要。

- [ ] **步骤 6：运行服务和 API 测试**

运行：

```bash
cd backend
.venv/bin/python -m pytest tests/integration/services/test_alert_source_service.py tests/api/test_alert_sources.py -q
```

预期：全部通过，Token 仅首次响应可见，管理操作可安全重放。

- [ ] **步骤 7：提交任务 2**

```bash
git add backend/src/incident_intelligence backend/tests
git commit -m "feat: 实现告警源与凭据管理"
```

---

### 任务 3：动态接入、真实验证与有界 Receipt

**文件：**

- 新建：`backend/src/incident_intelligence/services/source_authentication.py`
- 新建：`backend/src/incident_intelligence/services/source_receipts.py`
- 修改：`backend/src/incident_intelligence/adapters/alertmanager.py`
- 修改：`backend/src/incident_intelligence/adapters/cloudevents.py`
- 修改：`backend/src/incident_intelligence/api/dependencies.py`
- 修改：`backend/src/incident_intelligence/api/middleware.py`
- 修改：`backend/src/incident_intelligence/api/routes/alertmanager.py`
- 修改：`backend/src/incident_intelligence/api/routes/cloudevents.py`
- 修改：`backend/src/incident_intelligence/services/signal_intake.py`
- 修改：`backend/src/incident_intelligence/persistence/unit_of_work.py`
- 修改：`backend/src/incident_intelligence/main.py`
- 测试：`backend/tests/unit/services/test_source_authentication.py`
- 测试：`backend/tests/integration/services/test_source_receipts.py`
- 测试：`backend/tests/api/test_alertmanager_intake.py`
- 测试：`backend/tests/api/test_cloudevents_intake.py`
- 测试：`backend/tests/integration/services/test_source_isolation.py`
- 测试：`backend/tests/integration/test_external_alert_to_incident.py`

**接口：**

- `SourceAuthenticationService.authenticate(source_id, expected_type, bearer_token, now) -> AuthenticatedAlertSource`。
- `SignalIntakeService.ingest(commands, actor, request_id, receipt_context) -> SignalIntakeBatchResult`。
- `SourceReceiptService.record_authenticated_failure(source_id, outcome, reason_code, request_id, now) -> None`。
- 适配器新增可信参数 `alert_source_id`，但继续从外部负载计算 `source_event_id`、`source_instance` 和 `source_alert_key` 摘要。

- [ ] **步骤 1：写认证、隔离和验证入口失败测试**

```python
def test_same_external_identity_is_isolated_by_registered_source(api_context) -> None:
    first = post_same_alert(source_a, token_a)
    second = post_same_alert(source_b, token_b)
    assert first.json()["items"][0]["alert_id"] != second.json()["items"][0]["alert_id"]


def test_validation_persists_only_receipt(api_context) -> None:
    response = post_validation(source_a, token_a, valid_payload())
    assert response.status_code == 202
    assert count_rows("alert_source_receipts") == 1
    assert count_rows("signal_events") == 0
    assert count_rows("alerts") == 0
    assert count_rows("correlation_jobs") == 0
```

- [ ] **步骤 2：运行聚焦测试确认失败**

运行：

```bash
cd backend
.venv/bin/python -m pytest tests/unit/services/test_source_authentication.py tests/integration/services/test_source_receipts.py tests/integration/services/test_source_isolation.py tests/api/test_alertmanager_intake.py tests/api/test_cloudevents_intake.py -q
```

预期：动态路由、来源认证和 Receipt 尚不存在而失败。

- [ ] **步骤 3：实现来源认证**

解析 `iisrc_<credential_id>.<secret>`，用公开凭据 ID 定位记录，以 `secrets.compare_digest()` 比较 SHA-256 摘要，并依次检查来源归属、适配器类型、凭据状态和来源状态。未认证请求不写 Receipt；已认证但停用或类型不匹配请求写固定安全结果。

- [ ] **步骤 4：把 Receipt 纳入成功事务并实现失败小事务**

```python
@dataclass(frozen=True)
class ReceiptContext:
    alert_source_id: str
    adapter_type: str
    request_id: str
    received_at: datetime
```

`SignalIntakeService` 在成功事务内聚合 `SignalIntakeCounts`，插入一条 Receipt，更新来源计数和时间，并清理超 1,000 条或超过 30 天的旧记录。适配器失败通过 `SourceReceiptService` 独立记录 `PAYLOAD_REJECTED`；领域事务失败完整回滚后尽力记录 `PROCESSING_FAILED`。

- [ ] **步骤 5：实现动态普通入口和验证入口**

普通入口路径为 `/api/v1/intake/{adapter}/{alert_source_id}`，验证入口追加 `/validate`。两者复用认证、容量和适配器转换；验证入口不调用 `SignalIntakeService.ingest()`。固定入口继续使用原环境 Token，并注入对应系统兼容来源 ID。

- [ ] **步骤 6：补齐容量、失败和兼容回归**

验证动态 Alertmanager 256 KiB、CloudEvents 64 KiB、批次 100 条、Token 跨来源失败、撤销立即失败、停用后失败、旧兼容 Token 继续可用、错误响应不回显 Token 或 payload。

- [ ] **步骤 7：运行外部接入完整回归**

运行：

```bash
cd backend
.venv/bin/python -m pytest tests/unit/adapters tests/unit/services/test_source_authentication.py tests/integration/services/test_signal_intake_service.py tests/integration/services/test_source_receipts.py tests/integration/services/test_source_isolation.py tests/api/test_alertmanager_intake.py tests/api/test_cloudevents_intake.py tests/integration/test_external_alert_to_incident.py -q
```

预期：全部通过；来源隔离、重放、失败回滚和既有事故关联均保持正确。

- [ ] **步骤 8：提交任务 3**

```bash
git add backend/src/incident_intelligence backend/tests
git commit -m "feat: 接入独立告警源与接收记录"
```

---

### 任务 4：告警列表、统计和聚合详情 API

**文件：**

- 新建：`backend/src/incident_intelligence/persistence/alert_center_repository.py`
- 新建：`backend/src/incident_intelligence/services/alert_center.py`
- 新建：`backend/src/incident_intelligence/api/schemas/alerts.py`
- 新建：`backend/src/incident_intelligence/api/routes/alerts.py`
- 修改：`backend/src/incident_intelligence/api/dependencies.py`
- 修改：`backend/src/incident_intelligence/api/router.py`
- 修改：`backend/src/incident_intelligence/main.py`
- 测试：`backend/tests/integration/services/test_alert_center.py`
- 测试：`backend/tests/api/test_alerts.py`
- 测试：`backend/tests/api/test_resources.py`
- 测试：`backend/tests/api/test_correlation.py`

**接口：**

- `AlertCenterService.list_alerts(filters: AlertListFilters) -> AlertPage`。
- `AlertCenterService.summarize(window: Literal["1h", "24h", "7d"], now) -> AlertSummary`。
- `AlertCenterService.get_overview(alert_id: str) -> AlertOverview`。
- 现有 `/api/v1/alerts/{id}` 和 `/api/v1/alerts/{id}/correlation` 保持响应兼容。

- [ ] **步骤 1：写读取服务失败测试**

```python
def test_list_filters_by_source_service_time_and_incident_link(service) -> None:
    page = service.list_alerts(AlertListFilters(
        alert_source_id=SOURCE_A,
        service="payment-api",
        environment="production",
        incident_linked=False,
        limit=100,
        offset=0,
    ))
    assert [item.id for item in page.items] == [UNLINKED_PAYMENT_ALERT]


def test_overview_is_bounded_and_explains_correlation(service) -> None:
    overview = service.get_overview(ALERT_ID)
    assert len(overview.signals) == 100
    assert overview.signals_truncated is True
    assert overview.correlation.explanation
```

- [ ] **步骤 2：运行读取服务测试确认失败**

运行：

```bash
cd backend
.venv/bin/python -m pytest tests/integration/services/test_alert_center.py -q
```

预期：告警中心仓储和服务尚不存在而失败。

- [ ] **步骤 3：实现有界仓储查询和服务投影**

列表使用独立总数查询和稳定排序 `last_observed_at DESC, id DESC`；关键词最长 100 字符并转义 SQL 通配符；Overview 最多加载 101 条 SignalEvent 以计算截断，不读取 payload 指纹、幂等记录、凭据或审计正文。

- [ ] **步骤 4：写 API 契约失败测试**

覆盖全部筛选、非法枚举、非法时间窗口、`limit > 100`、统一未找到、统计固定窗口、详情字段白名单、关联失败状态和旧接口兼容。

- [ ] **步骤 5：实现路由和响应 Schema**

```python
@router.get("", response_model=AlertPageResponse)
def list_alerts(..., actor: Annotated[str, Depends(require_manual_actor)]) -> AlertPageResponse:
    ...

@router.get("/summary", response_model=AlertSummaryResponse)
def summarize_alerts(window: Literal["1h", "24h", "7d"] = "24h", ...) -> AlertSummaryResponse:
    ...

@router.get("/{alert_id}/overview", response_model=AlertOverviewResponse)
def get_alert_overview(alert_id: str, ...) -> AlertOverviewResponse:
    ...
```

静态 `/summary` 必须在动态 `/{alert_id}` 之前注册，避免被资源路由吞掉。

- [ ] **步骤 6：运行告警 API 与兼容回归**

运行：

```bash
cd backend
.venv/bin/python -m pytest tests/integration/services/test_alert_center.py tests/api/test_alerts.py tests/api/test_resources.py tests/api/test_correlation.py -q
```

预期：全部通过，列表、统计、Overview 和旧接口同时可用。

- [ ] **步骤 7：提交任务 4**

```bash
git add backend/src/incident_intelligence backend/tests
git commit -m "feat: 提供告警中心读取接口"
```

---

### 任务 5：前端共享请求边界与多页面导航

**文件：**

- 新建：`frontend/src/api/request.js`
- 新建：`frontend/src/api/request.test.js`
- 修改：`frontend/src/api/incidents.js`
- 修改：`frontend/src/api/incidents.test.js`
- 修改：`frontend/src/App.vue`
- 修改：`frontend/src/App.test.js`
- 修改：`frontend/src/styles.css`

**接口：**

- `ApiError(code, userMessage, status)` 替代事故专用网络错误基类；`IncidentApiError` 作为兼容别名或子类保留。
- `requestJson(path, options, messages)` 统一 JSON、非 JSON、网络失败和 AbortError 行为。
- `App.vue` 管理 `activeView: "incidents" | "alerts" | "alert-sources"`，不增加外部路由依赖。
- `openIncident(incidentId)` 从告警页切回事故中心并调用现有 `selectIncident()`。

- [ ] **步骤 1：写共享请求和导航失败测试**

```javascript
it("保留后端安全错误并把非结构化 5xx 转为服务不可用", async () => {
  fetch.mockResolvedValueOnce(new Response("bad gateway", { status: 502 }));
  await expect(requestJson("/api/v1/alerts", {}, { unavailable: "告警服务暂时不可用" }))
    .rejects.toMatchObject({ code: "api_unavailable", userMessage: "告警服务暂时不可用" });
});

it("点击告警导航显示真实告警页面而不是后续提示", async () => {
  await wrapper.get('[data-testid="nav-alerts"]').trigger("click");
  expect(wrapper.get('[data-testid="alert-center"]').exists()).toBe(true);
});
```

- [ ] **步骤 2：运行前端聚焦测试确认失败**

运行：

```bash
cd frontend
npm test -- src/api/request.test.js src/api/incidents.test.js src/App.test.js
```

预期：共享请求模块和真实页面导航尚不存在而失败。

- [ ] **步骤 3：抽取请求边界并保持事故 API 回归**

`requestJson()` 继续合并调用方 headers、保留 AbortError，并只信任后端字符串 `code/message`。事故接口导入共享函数，原事故错误文案和已有测试保持不变。

- [ ] **步骤 4：实现应用级页面选择**

`App.vue` 保留现有事故中心状态，只在 `activeView === "incidents"` 时展示事故工作区；告警和告警源页面使用独立组件占位挂载点。导航按钮具有稳定 `data-testid` 和 `aria-current="page"`。

- [ ] **步骤 5：运行事故中心前端回归**

运行：

```bash
cd frontend
npm test -- src/api/request.test.js src/api/incidents.test.js src/App.test.js src/composables/useIncidentCenter.test.js
```

预期：共享错误边界和三页导航通过，事故读取与处置行为不变。

- [ ] **步骤 6：提交任务 5**

```bash
git add frontend/src
git commit -m "refactor: 建立前端多页面请求边界"
```

---

### 任务 6：真实告警中心页面

**文件：**

- 新建：`frontend/src/api/alerts.js`
- 新建：`frontend/src/api/alerts.test.js`
- 新建：`frontend/src/presentation/alertView.js`
- 新建：`frontend/src/presentation/alertView.test.js`
- 新建：`frontend/src/composables/useAlertCenter.js`
- 新建：`frontend/src/composables/useAlertCenter.test.js`
- 新建：`frontend/src/components/AlertCenter.vue`
- 新建：`frontend/src/components/AlertCenter.test.js`
- 修改：`frontend/src/App.vue`
- 修改：`frontend/src/App.test.js`
- 修改：`frontend/src/styles.css`

**接口：**

- `fetchAlerts(filters, options)`、`fetchAlertSummary(window, options)`、`fetchAlertOverview(alertId, options)`。
- `toAlertListItem()`、`toAlertSummary()`、`toAlertDetail()` 只生成中文业务视图。
- `useAlertCenter()` 暴露筛选、列表/详情/统计状态、选中告警和重试方法。
- `AlertCenter.vue` 触发 `open-incident` 事件，参数仅为关联事故 ID。

- [ ] **步骤 1：写 API、展示转换和状态失败测试**

```javascript
it("将告警详情转换为检测结果和中文处理步骤", () => {
  const view = toAlertDetail(apiOverview());
  expect(view.resultText).toContain("18.4%");
  expect(view.steps.map((step) => step.title)).toEqual([
    "告警已认证接入", "重复信号已归并", "事故关联已完成",
  ]);
});

it("详情失败时保留已读取列表且不注入演示数据", async () => {
  fetchAlertOverview.mockRejectedValueOnce(new ApiError("api_unavailable", "暂时不可用"));
  await state.selectAlert("alt_real");
  expect(state.alerts.value).toHaveLength(1);
  expect(state.detailState.value).toBe("error");
});
```

- [ ] **步骤 2：运行告警中心测试确认失败**

运行：

```bash
cd frontend
npm test -- src/api/alerts.test.js src/presentation/alertView.test.js src/composables/useAlertCenter.test.js src/components/AlertCenter.test.js
```

预期：告警 API、视图转换、状态组合和页面组件尚不存在而失败。

- [ ] **步骤 3：实现 API 和纯展示转换**

URL 参数只发送非空筛选，`limit` 固定不超过 100。枚举转换集中在 `alertView.js`：`ACTIVE/RESOLVED/SUPPRESSED` 显示为“告警中/已恢复/已抑制”，内部规则码和 UUID 不作为默认正文。

- [ ] **步骤 4：实现可取消且防过期响应的状态组合**

复用事故中心的 AbortController、序列号和 250 毫秒防抖模式。列表为空时清空详情；详情失败只清空当前详情，不伪造告警；统计失败不阻断列表读取。

- [ ] **步骤 5：实现告警中心双栏页面**

按已确认页面稿实现概况、筛选、告警列表、实际检测结果、来源事实、系统处理过程、中文关联解释和事故跳转。桌面为列表/详情双栏，窄屏纵向排列；不出现告警确认、关闭或抑制操作。

- [ ] **步骤 6：运行页面和应用回归**

运行：

```bash
cd frontend
npm test -- src/api/alerts.test.js src/presentation/alertView.test.js src/composables/useAlertCenter.test.js src/components/AlertCenter.test.js src/App.test.js
npm run build
```

预期：告警页面真实读取、错误状态、事故跳转和生产构建全部通过。

- [ ] **步骤 7：提交任务 6**

```bash
git add frontend/src
git commit -m "feat: 实现真实告警中心"
```

---

### 任务 7：告警源管理页面与一次性 Token 流程

**文件：**

- 新建：`frontend/src/api/alertSources.js`
- 新建：`frontend/src/api/alertSources.test.js`
- 新建：`frontend/src/presentation/alertSourceView.js`
- 新建：`frontend/src/presentation/alertSourceView.test.js`
- 新建：`frontend/src/composables/useAlertSources.js`
- 新建：`frontend/src/composables/useAlertSources.test.js`
- 新建：`frontend/src/components/AlertSourceManager.vue`
- 新建：`frontend/src/components/AlertSourceDialog.vue`
- 新建：`frontend/src/components/AlertSourceManager.test.js`
- 修改：`frontend/src/App.vue`
- 修改：`frontend/src/App.test.js`
- 修改：`frontend/src/styles.css`

**接口：**

- `fetchAlertSources()`、`fetchAlertSource()`、`createAlertSource()`、`updateAlertSource()`、`rotateAlertSourceCredential()`、`revokeAlertSourceCredential()` 和 `fetchAlertSourceReceipts()`。
- 所有管理写请求由调用方生成一次 `Idempotency-Key`；网络结果未知时只允许使用原键手动重试。
- 创建和轮换的响应 Token 只保存在组件内存，关闭确认页后立即清空，不进入 localStorage、sessionStorage、URL 或日志。

- [ ] **步骤 1：写 API、状态和一次性秘密失败测试**

```javascript
it("网络结果未知时保留原幂等键且不自动生成第二个 Token", async () => {
  createAlertSource.mockRejectedValueOnce(new ApiError("api_unavailable", "结果未知"));
  await state.submitCreate({ name: "生产 Prometheus", source_type: "ALERTMANAGER" });
  const retryKey = state.retryableOperation.value.idempotencyKey;
  await state.retryLastOperation();
  expect(createAlertSource.mock.calls[1][1]).toBe(retryKey);
});

it("关闭一次性 Token 确认页后从状态中清除秘密", async () => {
  await wrapper.get('[data-testid="confirm-token-saved"]').trigger("click");
  expect(wrapper.text()).not.toContain(createdToken);
  expect(wrapper.vm.oneTimeToken).toBeNull();
});
```

- [ ] **步骤 2：运行告警源前端测试确认失败**

运行：

```bash
cd frontend
npm test -- src/api/alertSources.test.js src/presentation/alertSourceView.test.js src/composables/useAlertSources.test.js src/components/AlertSourceManager.test.js
```

预期：告警源客户端、状态和组件尚不存在而失败。

- [ ] **步骤 3：实现管理 API 和中文视图转换**

`WAITING_FIRST_DATA/RECEIVED/REJECTED/DISABLED` 分别转换为“等待首次数据/已收到数据/最近接收失败/已停用”。系统来源显示“系统管理”，隐藏修改、轮换和撤销动作。

- [ ] **步骤 4：实现安全写操作状态机**

每次新操作生成一个 UUID 幂等键；`api_unavailable` 时保存完整操作和原键供人工重试；版本冲突要求刷新；明确业务错误不可重试。创建/轮换成功只在内存保存一次性 Token。

- [ ] **步骤 5：实现来源列表、详情和对话框**

页面展示名称、类型、启用状态、接收状态、最近收到、计数、专属 Webhook、凭据元数据和 Receipt。创建需要名称和类型；停用需要确认；轮换后旧凭据继续显示有效；撤销最后凭据错误使用后端中文说明。

- [ ] **步骤 6：验证秘密不进入浏览器持久状态和构建**

测试拦截 `Storage.prototype.setItem`，证明 Token 不写存储；构建后扫描：

```bash
cd frontend
npm run build
if rg -n "II_FRONTEND_API_TOKEN|manual-api-client|scenario_id|experiment_id|iisrc_acr_" dist; then
  exit 1
fi
```

预期：测试通过，构建产物无内部认证主体、禁止字段或具体 Token 形态样本。

- [ ] **步骤 7：运行告警源页面和全前端回归**

运行：

```bash
cd frontend
npm test
npm run build
npm run test:sites
```

预期：全部通过，现有事故中心、告警中心和告警源管理互不破坏。

- [ ] **步骤 8：提交任务 7**

```bash
git add frontend/src
git commit -m "feat: 实现告警源管理页面"
```

---

### 任务 8：统一验证、真实联调与阶段验收

**文件：**

- 修改：`docs/current-state.md`
- 修改：`specs/active/alert-center-source-management.md`
- 新建：`docs/verification/2026-08-26-alert-center-source-management.md`
- 可能修改：`frontend/design-qa.md`，仅追加本阶段真实浏览器检查结果

**接口：**

- 统一验证脚本继续是 `scripts/verify-backend.sh`、`npm test`、`npm run build` 和 `npm run test:sites`。
- 活跃规格只有在全部验收条件均有证据时移动到 `specs/completed/alert-center-source-management.md`。

- [ ] **步骤 1：运行后端统一验证**

运行：

```bash
./scripts/verify-backend.sh
```

预期：Ruff、格式、Mypy、MySQL 测试和覆盖率门槛全部通过。

- [ ] **步骤 2：运行前端统一验证**

运行：

```bash
cd frontend
npm test
npm run build
npm run test:sites
```

预期：Vitest、生产构建和 Sites Worker 测试全部通过。

- [ ] **步骤 3：执行真实 MySQL 接入链路**

通过管理 API 创建两个同类型来源，分别使用各自一次性 Token 发送相同外部事件身份，断言：

```text
来源 A Alert ID != 来源 B Alert ID
精确重放前后 SignalEvent 数量不变
每次认证请求都有一条安全 Receipt
满足门槛的 Alert 最终关联 Incident
验证入口前后 SignalEvent/Alert/Incident 数量不变
```

不得把真实 Token 写入命令记录、验证文档或 shell 历史；使用当前终端环境变量并在验证完成后清除。

- [ ] **步骤 4：执行真实浏览器验收**

启动当前后端和 Vite 前端，验证：告警导航、筛选、详情、中文处理过程、事故跳转、来源创建、一次性 Token 确认、轮换、撤销、停用、等待首次数据、最近失败和服务不可用状态。检查浏览器控制台无错误或警告，页面无演示数据。

- [ ] **步骤 5：记录可复核验收证据**

验证文档记录命令、测试数量、覆盖率、迁移版本、浏览器旅程和安全扫描结论，只记录来源/告警/事故的非敏感 ID 和固定结果，不记录 Token、请求正文或外部 URL。

- [ ] **步骤 6：更新项目事实并完成规格**

只有步骤 1–5 全部通过后，通过 `apply_patch` 将 `specs/active/alert-center-source-management.md` 移动为 `specs/completed/alert-center-source-management.md`，并把状态改为“已验收”。同时在 `docs/current-state.md` 写明已实现能力、验证结果和仍未实现的自动取证、AI、生产身份与部署能力。

- [ ] **步骤 7：提交阶段验收**

```bash
git add docs/current-state.md docs/verification/2026-08-26-alert-center-source-management.md specs frontend/design-qa.md
git commit -m "docs: 完成告警中心与告警源管理验收"
```

---

## 计划自检结果

- 规格覆盖：来源模型、凭据、Receipt、动态接入、兼容入口、告警读取、两类前端页面、安全边界和真实验收均对应独立任务。
- 依赖顺序：任务 1 的来源身份是任务 2–4 的唯一前置；任务 4 的读取 API 是任务 6 前置；任务 2–3 的管理与接入 API 是任务 7 前置。
- 类型一致：来源 ID 统一使用 `src_`，凭据 ID 统一使用 `acr_`，一次性 Token 统一使用 `iisrc_<credential_id>.<secret>`。
- 范围检查：没有新增适配器、告警人工状态、AI、主动探测、SSO、RBAC、Pinia、路由器或 TypeScript 迁移。
- 占位检查：计划没有待定接口、模糊错误处理或未指定验证步骤。
