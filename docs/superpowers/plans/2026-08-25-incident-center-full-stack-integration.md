# 事故中心前后端完整联调 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让事故中心只展示 MySQL 中的真实事故聚合数据，并让认领操作在后端单事务持久化和审计。

**Architecture:** 后端新增事故中心专用仓储和应用服务，以固定数量查询形成有界列表与 overview，并用行锁实现幂等认领。前端通过同源 `/api/v1` 客户端和独立视图转换层加载数据；Vite 开发代理仅在服务端注入 Token，页面失败时不回退演示数据。

**Tech Stack:** Python 3、FastAPI、Pydantic、SQLAlchemy、Alembic、MySQL 8.4、Vue 3、Vite、Vitest。

**Spec:** `specs/active/incident-center-full-stack-integration.md`

## Global Constraints

- 所有变更直接提交到 `main`，禁止创建分支、worktree 或子 Agent。
- 浏览器不得持有共享 Bearer Token；禁止使用 `VITE_*` 暴露 Token。
- 页面不得保留或回退到静态事故演示数据。
- 新接口沿用人工控制面 Bearer Token，并保持分页、搜索、告警和时间线有界。
- 不返回 Secret、来源 URI、原始事件摘要、facts、任务租约或实验身份。
- 认领以认证主体为准，不接受浏览器自报负责人。
- 每个行为先看到聚焦测试按预期失败，再写最小实现并复验。

## 文件结构

- `backend/migrations/versions/0003_incident_assignment.py`：Incident 认领字段与数据库约束。
- `backend/src/incident_intelligence/persistence/models.py`：ORM 认领字段。
- `backend/src/incident_intelligence/persistence/incident_center_repository.py`：事故列表、聚合详情和锁定写入所需查询。
- `backend/src/incident_intelligence/services/incident_center.py`：读取用例、时间线转换和认领事务。
- `backend/src/incident_intelligence/api/schemas/incidents.py`：稳定的列表、overview 与认领响应契约。
- `backend/src/incident_intelligence/api/routes/incidents.py`：事故中心 HTTP 接口。
- `frontend/src/api/incidents.js`：同源 HTTP 客户端、安全错误和请求取消。
- `frontend/src/presentation/incidentView.js`：后端枚举、UTC 时间和聚合响应到中文视图模型的纯转换。
- `frontend/src/composables/useIncidentCenter.js`：列表/详情/认领状态、搜索防抖和过期响应丢弃。
- `frontend/src/App.vue`：只渲染组合式状态，不再定义事故数据。

---

### Task 1: Incident 认领字段与可逆迁移

**Files:**
- Create: `backend/migrations/versions/0003_incident_assignment.py`
- Modify: `backend/src/incident_intelligence/persistence/models.py`
- Modify: `backend/tests/integration/persistence/test_correlation_migration.py`
- Modify: `backend/tests/integration/persistence/test_constraints.py`

**Interfaces:**
- Produces: `IncidentRow.assignee: str | None`、`IncidentRow.claimed_at: datetime | None`。
- Produces: `ck_incidents_incident_assignment_pair`，保证两个字段同时为空或同时非空。

- [ ] **Step 1: 编写失败迁移测试**

```python
def test_assignment_migration_adds_nullable_pair_and_matches_orm(mysql_engine):
    upgrade_to("0003_incident_assignment")
    columns = inspected_columns(mysql_engine, "incidents")
    assert columns["assignee"].nullable is True
    assert columns["claimed_at"].nullable is True
    assert_schema_matches_metadata(mysql_engine)
```

- [ ] **Step 2: 编写失败约束测试**

```python
def test_incident_assignment_fields_must_be_both_null_or_both_present(session, incident):
    incident.assignee = "manual-api-client"
    incident.claimed_at = None
    with pytest.raises(IntegrityError):
        session.commit()
```

- [ ] **Step 3: 运行聚焦测试并确认因 `0003` 和字段缺失而失败**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/persistence/test_correlation_migration.py tests/integration/persistence/test_constraints.py -q`

Expected: FAIL，明确指向迁移或 `IncidentRow.assignee` 不存在。

- [ ] **Step 4: 实现最小迁移和 ORM 字段**

```python
assignee: Mapped[str | None] = mapped_column(String(128), nullable=True)
claimed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
```

迁移增加两个字段和以下检查约束，并在 downgrade 中按约束、字段逆序删除：

```sql
(assignee IS NULL AND claimed_at IS NULL)
OR (assignee IS NOT NULL AND claimed_at IS NOT NULL)
```

- [ ] **Step 5: 运行聚焦测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/persistence/test_correlation_migration.py tests/integration/persistence/test_constraints.py -q`

- [ ] **Step 6: 提交**

```bash
git add backend/migrations/versions/0003_incident_assignment.py backend/src/incident_intelligence/persistence/models.py backend/tests/integration/persistence/test_correlation_migration.py backend/tests/integration/persistence/test_constraints.py
git commit -m "feat: 增加事故认领持久化字段"
```

### Task 2: 有界事故列表与聚合详情服务

**Files:**
- Create: `backend/src/incident_intelligence/persistence/incident_center_repository.py`
- Create: `backend/src/incident_intelligence/services/incident_center.py`
- Create: `backend/tests/integration/services/test_incident_center.py`

**Interfaces:**
- Produces: `IncidentListQuery(environment, state, query, limit, offset)`。
- Produces: `IncidentCenterService.list_incidents(query) -> IncidentListResult`。
- Produces: `IncidentCenterService.get_overview(incident_id) -> IncidentOverview | None`。
- Consumes: Incident、IncidentAlertLink、Alert、ServiceCatalogEntry 和 CorrelationDecision 持久化记录。

- [ ] **Step 1: 编写真实 MySQL 列表失败测试**

```python
def test_list_incidents_filters_searches_and_orders_without_n_plus_one(service, seeded):
    result = service.list_incidents(
        IncidentListQuery(environment="production", state=None, query="payment", limit=20, offset=0)
    )
    assert result.total == 1
    assert result.items[0].id == seeded.payment_incident_id
    assert result.items[0].alert_count == 2
    assert result.items[0].owner_team == "payments"
```

- [ ] **Step 2: 编写聚合详情失败测试**

```python
def test_overview_contains_only_persisted_alerts_decision_and_timeline(service, seeded):
    overview = service.get_overview(seeded.payment_incident_id)
    assert overview is not None
    assert [item.id for item in overview.alerts] == seeded.linked_alert_ids
    assert overview.correlation.rule_version == "correlation.v1"
    assert overview.timeline[0].kind == "incident_created"
    assert "summary" not in overview.model_dump_json()
    assert "facts" not in overview.model_dump_json()
```

- [ ] **Step 3: 编写容量与固定查询数失败测试**

```python
def test_overview_caps_alerts_and_reports_truncation(service, seeded_with_101_alerts):
    overview = service.get_overview(seeded_with_101_alerts.incident_id)
    assert overview is not None
    assert len(overview.alerts) == 100
    assert overview.alerts_truncated is True
```

- [ ] **Step 4: 运行聚焦测试确认服务缺失失败**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_incident_center.py -q`

- [ ] **Step 5: 实现仓储固定查询与不可变视图模型**

```python
@dataclass(frozen=True)
class IncidentListQuery:
    environment: str | None
    state: str | None
    query: str | None
    limit: int
    offset: int

class IncidentCenterService:
    def list_incidents(self, query: IncidentListQuery) -> IncidentListResult:
        return self._read_list(query)

    def get_overview(self, incident_id: str) -> IncidentOverview | None:
        return self._read_overview(incident_id)
```

列表使用一条聚合查询和一条 count 查询；overview 使用 Incident、告警关联、服务目录、最新决策四类有界查询。时间线只使用 `Incident.created_at`、`IncidentAlertLink.linked_at` 和 `claimed_at`，按时间与稳定键排序。

- [ ] **Step 6: 运行聚焦测试确认通过并执行 Ruff/Mypy**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_incident_center.py -q`

Run: `cd backend && .venv/bin/python -m ruff check src tests migrations && .venv/bin/python -m mypy src migrations`

- [ ] **Step 7: 提交**

```bash
git add backend/src/incident_intelligence/persistence/incident_center_repository.py backend/src/incident_intelligence/services/incident_center.py backend/tests/integration/services/test_incident_center.py
git commit -m "feat: 增加事故中心聚合读取服务"
```

### Task 3: 事故列表与 overview HTTP 契约

**Files:**
- Create: `backend/src/incident_intelligence/api/schemas/incidents.py`
- Create: `backend/src/incident_intelligence/api/routes/incidents.py`
- Create: `backend/tests/api/test_incidents.py`
- Modify: `backend/src/incident_intelligence/api/dependencies.py`
- Modify: `backend/src/incident_intelligence/api/router.py`
- Modify: `backend/src/incident_intelligence/main.py`

**Interfaces:**
- Produces: `GET /api/v1/incidents`。
- Produces: `GET /api/v1/incidents/{incident_id}/overview`。
- Consumes: `IncidentCenterService.list_incidents` 和 `get_overview`。

- [ ] **Step 1: 编写列表与详情 API 失败测试**

```python
def test_incident_list_and_overview_return_bounded_contract(context):
    incident_id = seed_linked_incident(context)
    listed = context.client.get("/api/v1/incidents?environment=production&limit=20", headers=context.manual_headers)
    detail = context.client.get(f"/api/v1/incidents/{incident_id}/overview", headers=context.manual_headers)
    assert listed.status_code == 200
    assert set(listed.json()) == {"items", "total", "limit", "offset"}
    assert detail.status_code == 200
    assert "source_event_id" not in detail.text
```

- [ ] **Step 2: 编写认证、参数和未找到失败测试**

```python
@pytest.mark.parametrize("path", ["/api/v1/incidents", "/api/v1/incidents/inc_00000000000000000000000000000000/overview"])
def test_incident_center_requires_manual_token(context, path):
    assert context.client.get(path, headers=context.alertmanager_headers).status_code == 401

def test_incident_list_rejects_unbounded_query(context):
    assert context.client.get("/api/v1/incidents?limit=101", headers=context.manual_headers).status_code == 422
```

- [ ] **Step 3: 运行 API 测试确认路由缺失失败**

Run: `cd backend && .venv/bin/python -m pytest tests/api/test_incidents.py -q`

- [ ] **Step 4: 实现 Pydantic 响应和路由**

```python
@router.get("/api/v1/incidents", response_model=IncidentListResponse)
def list_incidents(
    environment: Environment | None = None,
    state: IncidentState | None = None,
    query: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    service: IncidentCenterService = Depends(get_incident_center_service),
) -> IncidentListResponse:
    result = service.list_incidents(
        IncidentListQuery(environment, state, query, limit, offset)
    )
    return IncidentListResponse.model_validate(result)
```

对 ID 使用现有 `INCIDENT_ID` 规则；不存在和非法格式统一 `resource_not_found`。

- [ ] **Step 5: 运行 API 测试和现有资源回归**

Run: `cd backend && .venv/bin/python -m pytest tests/api/test_incidents.py tests/api/test_resources.py -q`

- [ ] **Step 6: 提交**

```bash
git add backend/src/incident_intelligence/api/schemas/incidents.py backend/src/incident_intelligence/api/routes/incidents.py backend/src/incident_intelligence/api/dependencies.py backend/src/incident_intelligence/api/router.py backend/src/incident_intelligence/main.py backend/tests/api/test_incidents.py
git commit -m "feat: 提供事故中心读取接口"
```

### Task 4: 幂等、并发安全的真实认领

**Files:**
- Modify: `backend/src/incident_intelligence/persistence/incident_center_repository.py`
- Modify: `backend/src/incident_intelligence/services/incident_center.py`
- Modify: `backend/src/incident_intelligence/api/schemas/incidents.py`
- Modify: `backend/src/incident_intelligence/api/routes/incidents.py`
- Modify: `backend/tests/integration/services/test_incident_center.py`
- Modify: `backend/tests/api/test_incidents.py`

**Interfaces:**
- Produces: `IncidentCenterService.claim(incident_id, actor, request_id) -> IncidentClaimResult`。
- Produces: `POST /api/v1/incidents/{incident_id}/claim`。

- [ ] **Step 1: 编写认领事务失败测试**

```python
def test_claim_persists_actor_version_and_bounded_audit(service, seeded, session_factory):
    result = service.claim(seeded.incident_id, "manual-api-client", "req-claim")
    replay = service.claim(seeded.incident_id, "manual-api-client", "req-replay")
    assert result.assignee == "manual-api-client"
    assert replay.version == result.version
    assert audit_actions(session_factory, seeded.incident_id) == ["incident.claimed"]
```

- [ ] **Step 2: 编写冲突、状态、并发和回滚失败测试**

```python
def test_different_actor_cannot_take_claim(service, seeded):
    service.claim(seeded.incident_id, "actor-a", "req-a")
    with pytest.raises(IncidentAlreadyClaimed):
        service.claim(seeded.incident_id, "actor-b", "req-b")

def test_resolved_incident_cannot_be_claimed(service, resolved_incident):
    with pytest.raises(IncidentNotClaimable):
        service.claim(resolved_incident.id, "actor-a", "req-a")
```

并发测试使用两个 Session 同时认领，断言只有一个首次写入且至多一条审计；故障注入仓储在审计前抛错，断言 Incident 未变化。

- [ ] **Step 3: 运行聚焦测试确认 claim 缺失失败**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_incident_center.py -k claim -q`

- [ ] **Step 4: 实现行锁认领和安全异常**

```python
def claim(self, incident_id: str, actor: str, request_id: str) -> IncidentClaimResult:
    with self._uow_factory() as uow:
        incident = uow.incident_center.find_incident_for_update(incident_id)
        # 不存在、状态冲突、他人占用、同主体重放依次处理
        # 首次认领写 assignee/claimed_at/version 和一条有界审计
        uow.commit()
```

- [ ] **Step 5: 编写 claim API 失败测试并实现路由映射**

```python
def test_claim_endpoint_persists_and_replays(context, incident_id):
    first = context.client.post(f"/api/v1/incidents/{incident_id}/claim", headers=context.manual_headers)
    replay = context.client.post(f"/api/v1/incidents/{incident_id}/claim", headers=context.manual_headers)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
```

冲突固定映射为 `incident_already_claimed` 或 `incident_not_claimable` 的 409；数据库异常使用现有安全错误处理。

- [ ] **Step 6: 运行服务与 API 聚焦测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/services/test_incident_center.py tests/api/test_incidents.py -q`

- [ ] **Step 7: 提交**

```bash
git add backend/src/incident_intelligence/persistence/incident_center_repository.py backend/src/incident_intelligence/services/incident_center.py backend/src/incident_intelligence/api/schemas/incidents.py backend/src/incident_intelligence/api/routes/incidents.py backend/tests/integration/services/test_incident_center.py backend/tests/api/test_incidents.py
git commit -m "feat: 实现事故真实认领"
```

### Task 5: 前端 API 客户端与中文视图转换

**Files:**
- Create: `frontend/src/api/incidents.js`
- Create: `frontend/src/api/incidents.test.js`
- Create: `frontend/src/presentation/incidentView.js`
- Create: `frontend/src/presentation/incidentView.test.js`

**Interfaces:**
- Produces: `fetchIncidents(filters, { signal })`、`fetchIncidentOverview(id, { signal })`、`claimIncident(id)`。
- Produces: `toIncidentListItem(apiItem)`、`toIncidentDetail(overview, now)`。

- [ ] **Step 1: 编写客户端安全错误和取消失败测试**

```javascript
it("只请求同源 API 并把安全错误转换为用户消息", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ code: "service_unavailable", message: "事故数据暂时不可用" }),
    { status: 503, headers: { "Content-Type": "application/json" } },
  )));
  await expect(fetchIncidents({ environment: "production" }, {}))
    .rejects.toMatchObject({ code: "service_unavailable", userMessage: "事故数据暂时不可用" });
  expect(fetch).toHaveBeenCalledWith(expect.stringMatching(/^\/api\/v1\/incidents/), expect.any(Object));
});
```

- [ ] **Step 2: 编写枚举、时间和真实字段转换失败测试**

```javascript
it("把真实 overview 转为中文视图且不补造时间线", () => {
  const view = toIncidentDetail(apiOverview, new Date("2026-08-25T10:00:00Z"));
  expect(view.severity).toBe("严重");
  expect(view.state).toBe("待处置");
  expect(view.timeline).toHaveLength(apiOverview.timeline.length);
  expect(view.reason).toBe(apiOverview.correlation.explanation);
});
```

- [ ] **Step 3: 运行前端聚焦测试确认模块缺失失败**

Run: `cd frontend && npm test -- src/api/incidents.test.js src/presentation/incidentView.test.js`

- [ ] **Step 4: 实现最小客户端与纯转换层**

客户端只接受相对 URL、只解析 JSON 安全错误、对非 JSON/网络错误统一返回“事故数据暂时不可用，请稍后重试”。转换层固定维护严重度、事故状态、告警状态和来源中文映射，未知值显示“未知状态”而不是原样暴露枚举。

- [ ] **Step 5: 运行聚焦测试确认通过**

Run: `cd frontend && npm test -- src/api/incidents.test.js src/presentation/incidentView.test.js`

- [ ] **Step 6: 提交**

```bash
git add frontend/src/api/incidents.js frontend/src/api/incidents.test.js frontend/src/presentation/incidentView.js frontend/src/presentation/incidentView.test.js
git commit -m "feat: 增加事故中心前端 API 客户端"
```

### Task 6: 页面删除演示数据并接入真实请求状态

**Files:**
- Create: `frontend/src/composables/useIncidentCenter.js`
- Create: `frontend/src/composables/useIncidentCenter.test.js`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/App.test.js`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: Task 5 的三个 API 函数和两个视图转换函数。
- Produces: 列表/详情 `loading | ready | empty | error` 状态、`retryList()`、`selectIncident(id)`、`claimSelected()`。

- [ ] **Step 1: 重写 App 失败测试以使用真实 API 响应**

```javascript
vi.mock("./api/incidents", () => ({
  fetchIncidents: vi.fn(),
  fetchIncidentOverview: vi.fn(),
  claimIncident: vi.fn(),
}));

it("加载真实列表并选择后读取 overview", async () => {
  fetchIncidents.mockResolvedValue(apiList);
  fetchIncidentOverview.mockResolvedValue(apiOverview);
  const wrapper = mount(App);
  await flushPromises();
  expect(wrapper.text()).toContain(apiList.items[0].title);
  expect(fetchIncidentOverview).toHaveBeenCalledWith(apiList.items[0].id, expect.any(Object));
});
```

- [ ] **Step 2: 增加失败、空、重试、认领和过期响应测试**

```javascript
it("接口失败时展示失败与重试且绝不显示演示事故", async () => {
  fetchIncidents.mockRejectedValue({ userMessage: "事故数据暂时不可用，请稍后重试" });
  const wrapper = mount(App);
  await flushPromises();
  expect(wrapper.text()).toContain("事故数据暂时不可用");
  expect(wrapper.text()).not.toContain("支付接口错误率持续升高");
});
```

组合式测试另外覆盖：搜索 250ms 防抖、前一请求晚返回时被丢弃、认领成功后列表和详情均刷新、认领冲突显示安全消息。

- [ ] **Step 3: 运行页面测试确认仍依赖静态数组而失败**

Run: `cd frontend && npm test -- src/App.test.js src/composables/useIncidentCenter.test.js`

- [ ] **Step 4: 实现组合式状态并从 App 删除静态 `incidents` 常量**

```javascript
const {
  incidents, selectedIncident, listState, detailState, errorMessage,
  environment, search, selectIncident, retryList, claimSelected,
} = useIncidentCenter();
```

页面分别渲染加载骨架、真实空状态、失败重试和 ready 内容；无选中详情时不访问占位对象。顶部时间使用每分钟更新的浏览器本地时间。

- [ ] **Step 5: 运行页面与全部前端测试确认通过**

Run: `cd frontend && npm test`

- [ ] **Step 6: 扫描演示内容并提交**

Run: `rg "支付接口错误率持续升高|订单服务响应变慢|用户中心 Pod 反复重启|搜索服务 CPU 使用率过高" frontend/src`

Expected: 无输出。

```bash
git add frontend/src/App.vue frontend/src/App.test.js frontend/src/styles.css frontend/src/composables/useIncidentCenter.js frontend/src/composables/useIncidentCenter.test.js
git commit -m "feat: 事故中心接入真实后端数据"
```

### Task 7: 服务端代理、真实联调和阶段验收

**Files:**
- Modify: `frontend/vite.config.mjs`
- Create: `frontend/src/config/viteProxy.test.js`
- Modify: `frontend/AGENTS.md`
- Modify: `docs/current-state.md`
- Modify: `specs/active/incident-center-full-stack-integration.md`（验收后移动到 `specs/completed/`）

**Interfaces:**
- Consumes: `II_FRONTEND_API_URL` 和 `II_FRONTEND_API_TOKEN`，二者只在 Vite Node 进程读取。
- Produces: 浏览器同源 `/api` 到真实 FastAPI 的开发代理。

- [ ] **Step 1: 编写代理配置失败测试**

```javascript
it("代理只在服务端注入 Token 且客户端 define 中不含 Token", async () => {
  const config = createViteConfig({
    II_FRONTEND_API_URL: "http://127.0.0.1:8000",
    II_FRONTEND_API_TOKEN: "server-only-token",
  });
  expect(config.server.proxy["/api"].target).toBe("http://127.0.0.1:8000");
  expect(JSON.stringify(config.define ?? {})).not.toContain("server-only-token");
});
```

- [ ] **Step 2: 运行代理测试确认当前配置缺失失败**

Run: `cd frontend && npm test -- src/config/viteProxy.test.js`

- [ ] **Step 3: 导出 `createViteConfig(env)` 并实现服务端代理**

代理 `configure` 钩子只设置到上游请求的 `Authorization: Bearer <server-token>`；不记录 Token。未配置目标时保持无代理并由页面显示后端不可用。

- [ ] **Step 4: 执行迁移和启动真实服务**

Run: `cd backend && .venv/bin/alembic upgrade head`

Run backend with existing required `II_API_TOKEN`、`II_ALERTMANAGER_TOKEN`、`II_CLOUDEVENTS_TOKEN` and the real database URL. Run frontend with matching `II_FRONTEND_API_URL=http://127.0.0.1:8000` and `II_FRONTEND_API_TOKEN`，使用独立空闲端口。

- [ ] **Step 5: 执行真实 HTTP 冒烟**

使用已存在 Incident 时验证：列表 200、overview 200、首次 claim 200、重复 claim 200，数据库 assignee/version/audit 与响应一致。数据库为空时只验证列表真实返回 `items: []`，不写入演示事故。

- [ ] **Step 6: 执行浏览器验收**

在 1440×1024 验证真实列表、详情、认领或真实空状态；停止后端后验证失败提示和重试按钮，页面不得出现四条旧演示事故；恢复后端后重试成功；控制台无错误。

- [ ] **Step 7: 运行全量验证**

Run: `cd frontend && npm test && npm run build && npm run test:sites`

Run: `./scripts/verify-backend.sh`

Run: `rg -n "scenario_id|scenario_version|experiment_id|ground_truth|注入动作" backend/src frontend/src`

Expected: 业务源码无禁止身份命中。

- [ ] **Step 8: 更新能力状态和规格证据**

在 `docs/current-state.md` 记录真实读取、真实认领、代理边界、验证数据和已知缺口；规格补齐每条验收证据并移动到 `specs/completed/incident-center-full-stack-integration.md`。

- [ ] **Step 9: 最终提交**

```bash
git add frontend/vite.config.mjs frontend/src/config/viteProxy.test.js frontend/AGENTS.md docs/current-state.md specs/completed/incident-center-full-stack-integration.md
git commit -m "feat: 完成事故中心前后端联调"
```
