# 告警分组与风暴收敛实施计划

> **执行约束：** 本项目由当前会话在 `main` 分支顺序执行，不创建分支或 worktree，不使用子 Agent。所有步骤使用复选框跟踪，每个任务按测试驱动方式独立验收并提交。

**目标：** 在保留每条 SignalEvent 和 Alert 审计事实的前提下，把高可信相似告警持久化归入 AlertGroup，以告警组收敛事故关联任务，并让告警中心默认展示可理解、可分页的告警组和风暴摘要。

**架构：** 新增异步 Alert 分组管线和 AlertGroup 关联管线。信号接入事务不再为新变化直接创建 Alert 级事故关联任务，而是创建幂等分组任务；分组 Worker 使用确定性规则维护 AlertGroup 和成员关系，再以每组最多一个活动任务的方式调度事故关联。历史 Alert 级关联表和读取接口保留，逐条 `IncidentAlertLink` 继续作为最终审计关系。

**技术栈：** Python 3.13、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、MySQL 8.4、Vue 3、Vite、Vitest、Pytest、Playwright。

**规格：** `specs/active/alert-grouping-storm-convergence.md`

## 全局约束

- 所有修改直接提交到 `main`，不使用子 Agent、功能分支或 worktree。
- 主数据库继续使用 MySQL 8.4、InnoDB、utf8mb4、READ COMMITTED 和 UTC `DATETIME(6)`。
- SignalEvent、Alert、Incident 和 DiagnosisRun 保持独立；AlertGroup 是持久化运营投影，不进入外部接入契约。
- 首版归组规则固定为 `alert-grouping.v1`：同服务、同环境、同标准化症状、120 秒滚动窗口、唯一活跃候选。
- 60 秒新增 20 个成员进入 `STORM`，连续 5 分钟无新增成员恢复 `NORMAL`。
- Secret、原始 Webhook、无界标签、查询正文及故障实验身份不得进入新表、API、日志、审计或前端。
- 告警来源不是必要分组键；未知服务、未知症状、跨环境、窗口外和歧义候选安全创建独立组。
- 每项行为变更先写失败测试，再实现最小完整改动；每项提交前运行聚焦测试。
- 全部完成前必须运行 `./scripts/verify-backend.sh`、前端测试、前端构建和 Sites Worker 测试。

---

### 任务 1：修复告警和事故的分页、总数与截断口径

**文件：**

- 修改：`backend/src/incident_intelligence/services/incident_center.py`
- 修改：`backend/src/incident_intelligence/api/schemas/incidents.py`
- 修改：`backend/tests/integration/services/test_incident_center.py`
- 修改：`backend/tests/api/test_incidents.py`
- 修改：`frontend/src/composables/useAlertCenter.js`
- 修改：`frontend/src/presentation/incidentView.js`
- 修改：`frontend/src/IncidentCenterApp.vue`
- 修改：`frontend/src/components/AlertCenter.vue`
- 修改：`frontend/src/composables/useAlertCenter.test.js`
- 修改：`frontend/src/presentation/incidentView.test.js`
- 修改：`frontend/src/components/AlertCenter.test.js`

**接口：**

- 事故 Overview 新增 `alert_total: int`，`alerts` 仍最多返回 100 条。
- `useAlertCenter` 暴露 `total`、`offset`、`limit`、`hasPrevious`、`hasNext`、`goPrevious()` 和 `goNext()`。
- 页面文案使用后端总数，不再使用 `alerts.length` 代替总数。

- [ ] **步骤 1：写后端失败测试**

```python
overview = service.get_overview(incident_id, actor="manual-api-client")
assert overview.alert_total == 101
assert len(overview.alerts) == 100
assert overview.alerts_truncated is True
```

同时在 API 测试断言响应包含 `alert_total: 101`，而不是只返回当前数组长度。

- [ ] **步骤 2：运行聚焦测试并确认失败**

运行：

```bash
cd backend
.venv/bin/python -m pytest tests/integration/services/test_incident_center.py tests/api/test_incidents.py -q
```

预期：因 `alert_total` 尚不存在而失败。

- [ ] **步骤 3：实现后端真实总数**

在 `IncidentCenterRepository` 增加独立计数查询；`get_overview()` 同时返回真实总数、前 100 条成员和 `alerts_truncated = alert_total > len(alerts)`。计数不得通过读取 101 条后推断。

- [ ] **步骤 4：写前端失败测试并实现分页**

测试必须覆盖：总数 103、当前页 50 条、存在“1–50/103”和下一页按钮；进入事故详情时显示“共 101 条，当前展示 1–100 条”。实现时由 `fetchAlerts()` 传入 `limit` 与 `offset`，筛选条件变化后重置为第一页。

- [ ] **步骤 5：运行聚焦验证并提交**

```bash
cd frontend
npm test -- --run src/composables/useAlertCenter.test.js src/components/AlertCenter.test.js src/presentation/incidentView.test.js
```

提交：

```bash
git add backend/src/incident_intelligence/services/incident_center.py backend/src/incident_intelligence/api/schemas/incidents.py backend/tests/integration/services/test_incident_center.py backend/tests/api/test_incidents.py frontend/src/composables/useAlertCenter.js frontend/src/presentation/incidentView.js frontend/src/IncidentCenterApp.vue frontend/src/components/AlertCenter.vue frontend/src/composables/useAlertCenter.test.js frontend/src/presentation/incidentView.test.js frontend/src/components/AlertCenter.test.js
git commit -m "fix: 统一告警与事故数量口径"
```

### 任务 2：建立 AlertGroup 持久化模型和可降级迁移

**文件：**

- 创建：`backend/migrations/versions/0006_alert_grouping_storm.py`
- 修改：`backend/src/incident_intelligence/persistence/models.py`
- 修改：`backend/src/incident_intelligence/domain/enums.py`
- 修改：`backend/src/incident_intelligence/domain/models.py`
- 创建：`backend/tests/integration/persistence/test_alert_group_migration.py`
- 修改：`backend/tests/integration/persistence/test_constraints.py`
- 修改：`backend/tests/unit/domain/test_models.py`

**接口：**

- 新增 `AlertGroupState(ACTIVE, RESOLVED)` 和 `StormState(NORMAL, STORM)`。
- Alert 增加 `cycle: int`，首次为 1，重新触发时加 1。
- 新表：`alert_groups`、`alert_group_members`、`alert_grouping_jobs`、`alert_group_correlation_jobs`、`alert_group_decisions`。
- `incident_alert_links` 增加可空 `group_decision_id`，既有 `decision_id` 和历史数据保持有效。

- [ ] **步骤 1：写迁移失败测试**

```python
assert set(inspect(mysql_engine).get_table_names()) >= {
    "alert_groups",
    "alert_group_members",
    "alert_grouping_jobs",
    "alert_group_correlation_jobs",
    "alert_group_decisions",
}
assert "cycle" in {c["name"] for c in inspect(mysql_engine).get_columns("alerts")}
```

测试必须执行 `0005_alert_sources → head → 0005_alert_sources → head`，验证既有 Alert 的 `cycle=1`、ORM 元数据一致和完整降级。

- [ ] **步骤 2：运行迁移测试并确认失败**

```bash
cd backend
.venv/bin/python -m pytest tests/integration/persistence/test_alert_group_migration.py -q
```

- [ ] **步骤 3：实现表结构与约束**

关键约束：

```text
alert_group_members: UNIQUE(alert_id, alert_cycle)
alert_grouping_jobs: UNIQUE(alert_id, alert_version)
alert_group_correlation_jobs: UNIQUE(alert_group_id, active_slot)
```

`active_slot` 在 `PENDING/LEASED` 时固定为 1，终态为 NULL，利用 MySQL 多 NULL 唯一语义保证每组最多一个活动任务。所有计数非负，`active_count <= total_count`，规则原因码最多 10 个，候选事故最多 20 个。

- [ ] **步骤 4：补充 Alert cycle 领域行为测试与实现**

断言首次创建为 1、同轮更新与恢复保持 1、`RESOLVED → ACTIVE` 重开变成 2、迟到和重放不改变 cycle。

- [ ] **步骤 5：运行迁移、领域和约束测试并提交**

```bash
cd backend
.venv/bin/python -m pytest tests/integration/persistence/test_alert_group_migration.py tests/integration/persistence/test_constraints.py tests/unit/domain/test_models.py tests/unit/domain/test_signal_intake.py -q
```

提交：

```bash
git add backend/migrations/versions/0006_alert_grouping_storm.py backend/src/incident_intelligence/persistence/models.py backend/src/incident_intelligence/domain/enums.py backend/src/incident_intelligence/domain/models.py backend/tests/integration/persistence/test_alert_group_migration.py backend/tests/integration/persistence/test_constraints.py backend/tests/unit/domain/test_models.py backend/tests/unit/domain/test_signal_intake.py
git commit -m "feat: 建立告警组持久化模型"
```

### 任务 3：实现纯领域归组规则和资源身份转换

**文件：**

- 创建：`backend/src/incident_intelligence/domain/alert_grouping.py`
- 创建：`backend/tests/unit/domain/test_alert_grouping.py`
- 修改：`backend/src/incident_intelligence/domain/__init__.py`

**接口：**

- `GroupingContext`：Alert 当前事实、标准化症状、目录状态、既有成员组和候选组。
- `GroupingDecision`：`CREATE_GROUP`、`JOIN_GROUP` 或 `KEEP_GROUP`，包含组 ID、规则版本、固定原因码、解释和资源身份。
- `decide_alert_group(context: GroupingContext) -> GroupingDecision`。
- `derive_resource_identity(facts: Mapping[str, str], alert_id: str) -> ResourceIdentity`。

- [ ] **步骤 1：写完整决策表失败测试**

覆盖：唯一候选加入、无候选建组、多个候选安全建组、已有成员保持、跨环境、窗口外、未知服务、未知症状、不同来源仍可加入、历史 Incident 不兼容时拒绝加入、Pod/instance/node/container 资源身份优先级。

```python
decision = decide_alert_group(context(candidate_ids=("agr_1",)))
assert decision.action == "JOIN_GROUP"
assert decision.reason_codes == ("same_service_environment_symptom_window",)
```

- [ ] **步骤 2：运行测试并确认模块不存在**

```bash
cd backend
.venv/bin/python -m pytest tests/unit/domain/test_alert_grouping.py -q
```

- [ ] **步骤 3：实现无副作用规则**

规则函数不得访问数据库、时钟、UUID 或网络。窗口固定 120 秒；解释只能从固定中文模板生成，不拼接原始正文。

- [ ] **步骤 4：执行禁止身份和容量回归**

增加测试证明资源摘要和解释不会携带 `scenario_id`、实验动作、Token、来源 URI或超过字段上限的内容。

- [ ] **步骤 5：运行测试并提交**

```bash
cd backend
.venv/bin/python -m pytest tests/unit/domain/test_alert_grouping.py tests/unit/domain/test_forbidden_identity.py -q
git add src/incident_intelligence/domain/alert_grouping.py src/incident_intelligence/domain/__init__.py tests/unit/domain/test_alert_grouping.py
git commit -m "feat: 定义可解释告警归组规则"
```

### 任务 4：接入异步归组任务并维护告警组投影

**文件：**

- 创建：`backend/src/incident_intelligence/persistence/alert_group_repository.py`
- 创建：`backend/src/incident_intelligence/services/alert_grouping_jobs.py`
- 创建：`backend/src/incident_intelligence/services/alert_grouping.py`
- 创建：`backend/src/incident_intelligence/services/alert_grouping_runner.py`
- 修改：`backend/src/incident_intelligence/persistence/unit_of_work.py`
- 修改：`backend/src/incident_intelligence/services/signal_intake.py`
- 修改：`backend/src/incident_intelligence/main.py`
- 创建：`backend/tests/integration/services/test_alert_grouping_jobs.py`
- 创建：`backend/tests/integration/services/test_alert_grouping_service.py`
- 创建：`backend/tests/unit/services/test_alert_grouping_runner.py`
- 修改：`backend/tests/integration/services/test_signal_intake_service.py`

**接口：**

- `AlertGroupingJobLease(id, alert_id, alert_cycle, alert_version, attempts, ...)`。
- `AlertGroupingJobService.claim_batch()/fail()/retry_failed()` 沿用现有租约语义。
- `AlertGroupingService.process(lease) -> AlertGroupingResult`。
- 接入事务对每次有效 Alert 投影变化执行 `enqueue_grouping(alert_id, cycle, version)`，不再创建新的 Alert 级 `CorrelationJobRow`。

- [ ] **步骤 1：写接入失败测试**

```python
result = intake.submit_batch(commands, actor="alertmanager-adapter", request_id="req-1")
assert result.items[0].outcome == "opened"
assert count(AlertGroupingJobRow) == 1
assert count(CorrelationJobRow) == 0
```

重放、迟到和孤立恢复不得新增归组任务；归组任务数据库失败必须回滚该次接入事务，归组 Worker 处理失败不得删除已经接收的 SignalEvent 和 Alert。

- [ ] **步骤 2：实现仓储、任务租约和 Runner**

使用 `SELECT ... FOR UPDATE SKIP LOCKED` 领取；最多五次尝试；租约过期可接管；同一 Alert 版本唯一；单任务失败不阻断同批其他任务。

- [ ] **步骤 3：写投影失败测试**

测试 101 个不同 Alert：处理全部归组任务后只有一个 AlertGroup、101 个成员、`active_count=101`、`total_count=101`、`impacted_resource_count=101`，规则版本为 `alert-grouping.v1`。

- [ ] **步骤 4：实现投影、并发收敛和风暴状态**

归组事务按稳定顺序锁定候选组；唯一约束竞争后使用全新事务单次重试。成员加入或状态更新后重新计算有索引支持的组聚合；60 秒成员数达到 20 设置 `STORM`。Runner 每轮有界扫描连续 5 分钟无新增的 STORM 组并恢复为 NORMAL。

- [ ] **步骤 5：运行聚焦测试并提交**

```bash
cd backend
.venv/bin/python -m pytest tests/integration/services/test_signal_intake_service.py tests/integration/services/test_alert_grouping_jobs.py tests/integration/services/test_alert_grouping_service.py tests/unit/services/test_alert_grouping_runner.py -q
git add src/incident_intelligence/domain/models.py src/incident_intelligence/persistence/alert_group_repository.py src/incident_intelligence/persistence/unit_of_work.py src/incident_intelligence/services/alert_grouping.py src/incident_intelligence/services/alert_grouping_jobs.py src/incident_intelligence/services/alert_grouping_runner.py src/incident_intelligence/services/signal_intake.py src/incident_intelligence/main.py tests/integration/services/test_signal_intake_service.py tests/integration/services/test_alert_grouping_jobs.py tests/integration/services/test_alert_grouping_service.py tests/unit/services/test_alert_grouping_runner.py
git commit -m "feat: 接入异步告警归组管线"
```

### 任务 5：实现告警组关联任务收敛和事故关联

**文件：**

- 创建：`backend/src/incident_intelligence/domain/alert_group_correlation.py`
- 创建：`backend/src/incident_intelligence/services/alert_group_correlation_jobs.py`
- 创建：`backend/src/incident_intelligence/services/alert_group_correlation.py`
- 修改：`backend/src/incident_intelligence/services/correlation_runner.py`
- 修改：`backend/src/incident_intelligence/services/correlation.py`
- 修改：`backend/src/incident_intelligence/persistence/alert_group_repository.py`
- 修改：`backend/src/incident_intelligence/main.py`
- 创建：`backend/tests/unit/domain/test_alert_group_correlation.py`
- 创建：`backend/tests/integration/services/test_alert_group_correlation_jobs.py`
- 创建：`backend/tests/integration/services/test_alert_group_correlation_service.py`
- 修改：`backend/tests/integration/services/test_correlation_service.py`
- 修改：`backend/tests/integration/test_external_alert_to_incident.py`

**接口：**

- `AlertGroupCorrelationJobService.schedule(group_id, target_version, now)` 更新既有 PENDING 任务或创建一个活动任务。
- `AlertGroupCorrelationLease(id, alert_group_id, target_group_version, ...)`。
- `AlertGroupCorrelationService.process(lease) -> AlertGroupCorrelationResult`。
- 历史 `CorrelationService` 和 Alert 级任务读取保持可用，只停止为新 Alert 创建任务。

- [ ] **步骤 1：写任务合并失败测试**

```python
for version in range(1, 102):
    jobs.schedule(group_id, target_version=version, now=NOW)
assert count_active_jobs(group_id) == 1
assert pending_job.target_group_version == 101
```

补充 LEASED 期间更新、完成后补跑一次、租约过期接管、五次失败和并发调度测试。

- [ ] **步骤 2：实现活动槽和目标版本调度**

PENDING 任务原位提升目标版本；LEASED 时只提升组的 `desired_correlation_version`；处理完成发现版本落后时创建一个后继 PENDING 任务。终态任务清空 `active_slot`。

- [ ] **步骤 3：写事故关联失败测试**

101 成员的同一组处理后断言：

```python
assert count(IncidentRow) == 1
assert count(IncidentAlertLinkRow) == 101
assert count(AlertGroupDecisionRow) == 1
assert count_active_group_jobs(group_id) == 0
assert session.get(AlertGroupRow, group_id).incident_id == incident_id
```

同时验证错误率、延迟、Pod 重启三个组可以依据事故候选规则进入同一事故；跨环境和歧义候选不误合并。

- [ ] **步骤 4：实现组关联与历史兼容**

新建事故继续选择代表 Alert 作为 `primary_alert_id`；组决策写入独立不可变表；每个成员建立现有 `IncidentAlertLink` 并填写 `group_decision_id`。Alert 详情查询优先读取所属组决策，没有组时回退历史 Alert 级决策。

- [ ] **步骤 5：验证并提交**

```bash
cd backend
.venv/bin/python -m pytest tests/unit/domain/test_alert_group_correlation.py tests/integration/services/test_alert_group_correlation_jobs.py tests/integration/services/test_alert_group_correlation_service.py tests/integration/services/test_correlation_service.py tests/integration/test_external_alert_to_incident.py -q
git add src/incident_intelligence/domain/alert_group_correlation.py src/incident_intelligence/services/alert_group_correlation_jobs.py src/incident_intelligence/services/alert_group_correlation.py src/incident_intelligence/services/correlation_runner.py src/incident_intelligence/services/correlation.py src/incident_intelligence/persistence/alert_group_repository.py src/incident_intelligence/main.py tests/unit/domain/test_alert_group_correlation.py tests/integration/services/test_alert_group_correlation_jobs.py tests/integration/services/test_alert_group_correlation_service.py tests/integration/services/test_correlation_service.py tests/integration/test_external_alert_to_incident.py
git commit -m "feat: 按告警组收敛事故关联任务"
```

### 任务 6：安全迁移历史告警和排空旧任务

**文件：**

- 创建：`backend/src/incident_intelligence/services/alert_group_backfill.py`
- 修改：`backend/src/incident_intelligence/main.py`
- 修改：`backend/src/incident_intelligence/settings.py`
- 创建：`backend/tests/integration/services/test_alert_group_backfill.py`
- 修改：`backend/tests/unit/test_settings.py`

**接口：**

- `AlertGroupBackfillService.enqueue_batch(limit: int, now: datetime) -> int`。
- 只为尚无当前 cycle 成员关系、且没有 PENDING/LEASED 历史 Alert 级关联任务的 Alert 创建归组任务。
- 已有 IncidentAlertLink 的 Alert 归组后继承既有 Incident，不再次创建事故。

- [ ] **步骤 1：写历史兼容失败测试**

准备三类历史数据：已关联、旧任务待处理、旧任务已完成但未关联。断言待处理旧任务先排空；已关联 Alert 的组采用原事故；全部完成后没有未分组 Alert，也没有重复 Incident。

- [ ] **步骤 2：实现有界回填**

每轮最多处理 100 条，使用稳定 ID 排序和 `SKIP LOCKED`；不在 Alembic 数据迁移中依据部署时间直接合并历史告警。

- [ ] **步骤 3：接入生命周期并验证可停止恢复**

同一 `correlation_runner_enabled` 控制旧任务排空、归组和组关联三个循环。进程中断后依靠持久任务继续，不使用内存队列。

- [ ] **步骤 4：运行历史兼容回归**

```bash
cd backend
.venv/bin/python -m pytest tests/integration/services/test_alert_group_backfill.py tests/integration/services/test_correlation_service.py tests/unit/test_settings.py -q
```

- [ ] **步骤 5：提交**

```bash
git add src/incident_intelligence/services/alert_group_backfill.py src/incident_intelligence/main.py src/incident_intelligence/settings.py tests/integration/services/test_alert_group_backfill.py tests/unit/test_settings.py
git commit -m "feat: 安全迁移历史告警关联任务"
```

### 任务 7：提供告警组列表、详情、成员分页和概况 API

**文件：**

- 创建：`backend/src/incident_intelligence/services/alert_group_center.py`
- 创建：`backend/src/incident_intelligence/api/schemas/alert_groups.py`
- 创建：`backend/src/incident_intelligence/api/routes/alert_groups.py`
- 修改：`backend/src/incident_intelligence/api/dependencies.py`
- 修改：`backend/src/incident_intelligence/api/router.py`
- 修改：`backend/src/incident_intelligence/main.py`
- 修改：`backend/src/incident_intelligence/services/alert_center.py`
- 修改：`backend/src/incident_intelligence/persistence/alert_center_repository.py`
- 修改：`backend/src/incident_intelligence/services/incident_center.py`
- 修改：`backend/src/incident_intelligence/persistence/incident_center_repository.py`
- 修改：`backend/src/incident_intelligence/api/schemas/incidents.py`
- 修改：`backend/src/incident_intelligence/api/routes/incidents.py`
- 创建：`backend/tests/integration/services/test_alert_group_center.py`
- 创建：`backend/tests/api/test_alert_groups.py`
- 修改：`backend/tests/api/test_alerts.py`
- 修改：`backend/tests/integration/services/test_incident_center.py`
- 修改：`backend/tests/api/test_incidents.py`

**接口：**

- `GET /api/v1/alert-groups`
- `GET /api/v1/alert-groups/summary?window=24h`
- `GET /api/v1/alert-groups/{group_id}/overview`
- `GET /api/v1/alert-groups/{group_id}/alerts?limit=50&offset=0`
- `GET /api/v1/incidents/{incident_id}/alert-groups?limit=20&offset=0`
- Alert Overview 新增可空 `group` 摘要；全部读取只接受人工控制面 Token。

- [ ] **步骤 1：写 API 契约失败测试**

断言列表筛选、真实 total、最大 limit 100、offset 上限、无效 ID 统一 404、未认证 401、成员第 101 条可通过第二页读取；事故告警组接口返回真实组总数并独立分页。

- [ ] **步骤 2：写概况和安全失败测试**

概况必须返回 `active_groups`、`severe_active_groups`、`active_alerts`、`storm_groups`、`resolved_groups`、`compression_ratio`、`peak_rate_per_minute` 和 `pending_group_jobs`。扫描响应不存在 Secret、来源 URI、原始 Webhook 和禁止实验身份。

- [ ] **步骤 3：实现查询服务与仓储**

列表查询必须由数据库排序、筛选、分页和计数；成员来源分布、严重度分布和影响资源 Top 20 使用有界聚合查询；不得加载全部成员后在 Python 中分页。

- [ ] **步骤 4：实现路由、依赖注入和 Alert 详情兼容**

更新 `create_app()` 注册 `AlertGroupCenterService`，更新路由和 Schema；Alert 详情在有成员关系时返回组 ID、标题、成员总数、风暴状态和关联事故；Incident Overview 继续返回前 100 条兼容 Alert，同时由独立接口分页返回关联告警组。

- [ ] **步骤 5：运行验证并提交**

```bash
cd backend
.venv/bin/python -m pytest tests/integration/services/test_alert_group_center.py tests/api/test_alert_groups.py tests/api/test_alerts.py tests/integration/services/test_incident_center.py tests/api/test_incidents.py -q
git add src/incident_intelligence/services/alert_group_center.py src/incident_intelligence/api/schemas/alert_groups.py src/incident_intelligence/api/routes/alert_groups.py src/incident_intelligence/api/dependencies.py src/incident_intelligence/api/router.py src/incident_intelligence/main.py src/incident_intelligence/services/alert_center.py src/incident_intelligence/persistence/alert_center_repository.py src/incident_intelligence/services/incident_center.py src/incident_intelligence/persistence/incident_center_repository.py src/incident_intelligence/api/schemas/incidents.py src/incident_intelligence/api/routes/incidents.py tests/integration/services/test_alert_group_center.py tests/api/test_alert_groups.py tests/api/test_alerts.py tests/integration/services/test_incident_center.py tests/api/test_incidents.py
git commit -m "feat: 提供告警组读取接口"
```

### 任务 8：把告警中心改造成组优先界面

**文件：**

- 创建：`frontend/src/api/alertGroups.js`
- 创建：`frontend/src/api/alertGroups.test.js`
- 修改：`frontend/src/api/incidents.js`
- 修改：`frontend/src/api/incidents.test.js`
- 创建：`frontend/src/composables/useAlertGroupCenter.js`
- 创建：`frontend/src/composables/useAlertGroupCenter.test.js`
- 创建：`frontend/src/presentation/alertGroupView.js`
- 创建：`frontend/src/presentation/alertGroupView.test.js`
- 创建：`frontend/src/components/AlertGroupCenter.vue`
- 创建：`frontend/src/components/AlertGroupCenter.test.js`
- 修改：`frontend/src/components/AlertCenter.vue`
- 修改：`frontend/src/IncidentCenterApp.vue`
- 修改：`frontend/src/presentation/incidentView.js`
- 修改：`frontend/src/styles.css`
- 修改：`frontend/src/App.test.js`

**接口：**

- 告警中心默认挂载 `AlertGroupCenter`。
- `AlertCenter` 保留为“原始告警”次级视图。
- 组列表和成员列表分别维护分页、加载、空、失败和重试状态。

- [x] **步骤 1：写展示转换失败测试**

断言 101 个成员转换为“101 条原始告警”“影响 101 个资源”“告警风暴”“已关联事故”，压缩率和时间不能由当前成员页长度推断。

- [x] **步骤 2：写组件旅程失败测试**

覆盖默认组列表、状态筛选、搜索防抖、选择组、查看归组解释、来源/严重度分布、成员第二页、进入事故、切换原始告警、API 失败不回退演示数据。

- [x] **步骤 3：实现 API、状态和展示层**

请求层保持统一错误契约和 AbortController；筛选变化重置 offset；过期响应不得覆盖新查询；组、成员和事故告警组分页状态互相独立。

- [x] **步骤 4：实现非技术化页面**

默认卡片直接回答服务、症状、持续时间、原始告警数、影响资源数、风暴状态和事故关系。技术规则版本默认不显示；归组解释使用中文业务文案；原始成员置于可展开分页区。

- [x] **步骤 5：运行前端验证并提交**

```bash
cd frontend
npm test
npm run build
npm run test:sites
git add src/api/alertGroups.js src/api/alertGroups.test.js src/api/incidents.js src/api/incidents.test.js src/composables/useAlertGroupCenter.js src/composables/useAlertGroupCenter.test.js src/presentation/alertGroupView.js src/presentation/alertGroupView.test.js src/components/AlertGroupCenter.vue src/components/AlertGroupCenter.test.js src/components/AlertCenter.vue src/IncidentCenterApp.vue src/presentation/incidentView.js src/styles.css src/App.test.js
git commit -m "feat: 实现组优先告警中心"
```

### 任务 9：完成 101/1,000 条风暴验收、浏览器检查和文档封存

**文件：**

- 创建：`backend/tests/integration/test_alert_storm_convergence.py`
- 创建：`docs/verification/2026-08-26-alert-grouping-storm-convergence.md`
- 修改：`docs/current-state.md`
- 修改：`specs/active/alert-grouping-storm-convergence.md`
- 最终移动：`specs/active/alert-grouping-storm-convergence.md` → `specs/completed/alert-grouping-storm-convergence.md`

**验收输出：**

- 101 条真实 Alertmanager 告警：101 SignalEvent、101 Alert、1 AlertGroup、1 Incident、一个同时可领取的组关联任务。
- 1,000 条并发相似告警：无重复成员、组或活动任务。
- 告警中心可以访问第 101 条成员；事故列表与详情都显示 101。

- [ ] **步骤 1：写完整真实 MySQL 风暴测试**

测试必须发送不同 fingerprint、Pod 和标题的同服务同症状告警，验证分批 100+1、精确重放、跨来源归组、跨环境隔离、三症状分组、恢复不关闭事故和失败重试。

- [ ] **步骤 2：运行统一后端验收**

```bash
./scripts/verify-backend.sh
```

要求 Ruff、格式、Mypy、全部 Pytest 和覆盖率门槛通过。

- [ ] **步骤 3：运行统一前端验收**

```bash
cd frontend
npm test
npm run build
npm run test:sites
```

- [ ] **步骤 4：执行真实浏览器旅程**

在真实 MySQL、真实后端和 Vite 页面中验证：默认只出现一个风暴告警组；展开后可以翻页到第 101 条；组卡和事故页面均显示 101；页面没有控制台错误、UUID、Token、来源 URI或实验身份。

- [ ] **步骤 5：封存证据、完成规格并提交**

把命令、通过数量、覆盖率、数据库计数、浏览器截图结论和已知缺口写入验证文档；更新 `docs/current-state.md`；逐条填写规格验证证据并移入 completed。

```bash
git add backend/tests/integration/test_alert_storm_convergence.py docs/verification/2026-08-26-alert-grouping-storm-convergence.md docs/current-state.md specs/completed/alert-grouping-storm-convergence.md
git commit -m "docs: 完成告警分组与风暴收敛验收"
```

## 最终完成标准

- 规格中的每条验收条件都有自动测试或真实浏览器证据；
- 新告警不再创建 Alert 级关联任务，历史任务可安全排空和读取；
- 101 条不同告警最终形成 1 个告警组和 1 个事故，并且所有原始事实可分页访问；
- 同一告警组始终最多存在一个可领取关联任务；
- 1,000 条并发测试无重复、误合并或数据丢失；
- 后端统一验证、前端测试、构建和 Sites Worker 全部通过；
- 当前状态和完成规格准确区分已实现能力与剩余缺口。
