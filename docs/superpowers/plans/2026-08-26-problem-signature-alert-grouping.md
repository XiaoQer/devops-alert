# 问题签名告警归集实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 将告警组从资源实例归组升级为问题签名归组，使同一 Namespace 的多 Pod 同类告警收敛为一个可解释问题组。

**架构：** 新增纯领域 `ProblemSignature`，由规范化问题类型、症状、环境、逻辑来源和影响范围生成稳定摘要。Alert 保留真实资源实体，AlertGroup 单独持久化问题签名；归组查询问题键，事故关联继续独立处理。

**技术栈：** Python、FastAPI、Pydantic、SQLAlchemy、Alembic、MySQL 8.4、Vue 3、Vitest、Pytest。

**规格：** `specs/active/problem-signature-alert-grouping.md`。

## 全局约束

- 直接在 main 开发，不创建分支、worktree 或子 Agent；
- 每个行为先编写并观察失败测试；
- 不访问 Kubernetes 或 Prometheus，不新增凭据、RBAC 或 service 解析 Worker；
- 不修改 Alertmanager 的来源幂等身份；
- 不自动重写已关联事故或已解决历史组；
- Secret、原始 Webhook 和未知标签不得进入持久化、日志或 API。

---

### 任务 1：建立问题签名纯领域模型

**文件：**
- 新建：`backend/src/incident_intelligence/domain/problem_signatures.py`
- 新建：`backend/tests/unit/domain/test_problem_signatures.py`

**接口：**
- 输入：`alert_source_id`、`problem_type`、`symptom`、`environment` 和白名单 facts；
- 输出：`ProblemSignature(problem_key, problem_type, scope_type, scope_key, scope_display_name, window_seconds, version)`；
- 提供：`derive_problem_signature(...) -> ProblemSignature`。

- [ ] 编写失败测试，覆盖 SERVICE、WORKLOAD、NAMESPACE、CLUSTER、JOB、SOURCE 六种范围和固定优先级；
- [ ] 编写失败测试，证明 Pod、Node、Instance、Container 名变化不改变同范围问题键；
- [ ] 编写失败测试，证明来源、Namespace、alertname、环境或症状变化会改变问题键；
- [ ] 实现有界规范化、范围摘要和 `problem-signature.v1` SHA-256；
- [ ] 运行领域测试、Ruff、格式和 Mypy；
- [ ] 提交 `feat: 建立问题签名领域模型`。

### 任务 2：扩展安全事实与数据库模型

**文件：**
- 修改：`backend/src/incident_intelligence/adapters/alertmanager.py`
- 修改：`backend/src/incident_intelligence/persistence/models.py`
- 新建：`backend/migrations/versions/0008_problem_signature_grouping.py`
- 修改：`backend/tests/unit/adapters/test_alertmanager.py`
- 新建：`backend/tests/integration/persistence/test_problem_signature_migration.py`

**接口：**
- Alertmanager facts 新增 `alertname`、`workload` 白名单键；
- AlertGroup 新增 `problem_key`、`problem_type`、`scope_type`、`scope_key`、`scope_display_name`、`signature_version`。

- [ ] 编写失败测试，证明 alertname/workload 被有界保存，未知标签继续忽略；
- [ ] 编写失败迁移测试，覆盖空库升级、既有组回填、索引、约束和往返；
- [ ] 实现 0008 迁移，从代表 Alert 与最新 SignalEvent 的安全字段回填问题签名；
- [ ] 增加 `(state, problem_key, last_observed_at)` 候选索引和字段约束；
- [ ] 运行适配器、迁移、ORM 一致性、Ruff、格式和 Mypy；
- [ ] 提交 `feat: 持久化告警组问题签名`。

### 任务 3：把归组引擎切换到问题键

**文件：**
- 修改：`backend/src/incident_intelligence/domain/alert_grouping.py`
- 修改：`backend/src/incident_intelligence/services/alert_grouping.py`
- 修改：`backend/src/incident_intelligence/persistence/alert_group_repository.py`
- 修改：`backend/tests/unit/domain/test_alert_grouping.py`
- 修改：`backend/tests/integration/services/test_alert_grouping_service.py`
- 修改：`backend/tests/integration/test_alert_storm_convergence.py`

**接口：**
- `GroupingContext` 和 `AlertGroupCandidate` 使用 `problem_key`、`scope_type` 和 `window_seconds`；
- `active_candidates(problem_key, observed_from, observed_to, limit)` 只返回相同问题签名的活动组。

- [ ] 编写失败测试：6 个不同 Pod、同 Namespace 的 KubePodNotReady 形成 CREATE + 5 个 JOIN；
- [ ] 编写失败测试：不同 Namespace、alertname、环境、来源分别隔离；
- [ ] 编写失败测试：unknown 症状但问题类型相同时允许归组；
- [ ] 实现问题键候选查询、范围窗口和新的固定中文原因码；
- [ ] 保留已有成员、事故冲突、多候选和服务风暴安全行为；
- [ ] 运行归组、风暴和关联回归；
- [ ] 提交 `feat: 按问题签名归集告警`。

### 任务 4：安全处理无服务事故关联

**文件：**
- 修改：`backend/src/incident_intelligence/services/alert_group_correlation.py`
- 修改：`backend/src/incident_intelligence/domain/correlation.py`
- 修改：`backend/tests/integration/services/test_alert_group_correlation_service.py`
- 修改：`backend/tests/unit/domain/test_correlation.py`

**接口：**
- 无 service 组生成 `SKIPPED_SERVICE_MISSING` 决策；
- 决策保存组版本、问题签名版本、固定事实与中文解释，不创建 Incident。

- [ ] 编写失败测试，证明无服务组完成关联任务但 Incident 数为零；
- [ ] 编写失败测试，证明重复处理幂等且失败事务完整回滚；
- [ ] 实现固定跳过决策并保持有服务规则不变；
- [ ] 运行关联领域、服务和 API 回归；
- [ ] 提交 `feat: 安全跳过无服务事故关联`。

### 任务 5：实现有界重新归组

**文件：**
- 新建：`backend/src/incident_intelligence/services/alert_regrouping.py`
- 修改：`backend/src/incident_intelligence/persistence/alert_group_repository.py`
- 新建：`backend/tests/integration/services/test_alert_regrouping.py`
- 按现有管理 API 模式新增受认证重新归组入口及测试。

**接口：**
- `regroup_active_unlinked_groups(limit: int, actor: str, request_id: str) -> RegroupResult`；
- 仅处理 ACTIVE、service 为空、incident_id 为空、旧规则版本的组，单批最多 100 个成员。

- [ ] 编写失败测试，覆盖成员合并、旧组解决、计数刷新和有界审计；
- [ ] 编写失败测试，证明已关联事故、已解决组和新版本组不被修改；
- [ ] 实现行锁、幂等请求、单事务移动和固定结果码；
- [ ] 运行并发收敛、回滚和重放测试；
- [ ] 提交 `feat: 支持活动告警安全重新归组`。

### 任务 6：更新 API 与中文前端

**文件：**
- 修改：`backend/src/incident_intelligence/api/schemas/alert_groups.py`
- 修改：`backend/src/incident_intelligence/services/alert_group_center.py`
- 修改：`frontend/src/presentation/alertGroupView.js`
- 修改对应后端 API、前端展示与测试文件。

**接口：**
- 告警组列表和详情返回 `problem_type`、`scope_type`、`scope_display_name` 和实体分布；
- 前端显示“服务未提供”“Namespace · 名称”“6 个 Pod”。

- [ ] 编写失败 API 测试，验证问题类型、范围和资源统计；
- [ ] 编写失败前端测试，验证空 service 中文文案和 KubePodNotReady 聚合卡片；
- [ ] 实现后端白名单响应和前端中文映射；
- [ ] 运行 API、Vitest、构建和浏览器旅程；
- [ ] 提交 `feat: 展示问题签名归集结果`。

### 任务 7：真实数据升级与统一验收

**文件：**
- 修改：`docs/current-state.md`
- 修改：`docs/architecture.md`
- 修改：`specs/active/problem-signature-alert-grouping.md`

- [ ] 升级本地 MySQL 到 0008 并验证就绪；
- [ ] 对当前 6 个无服务活动组执行一次有界重新归组；
- [ ] 验证页面形成 1 个 KubePodNotReady 组、6 条原始告警和 6 个 Pod；
- [ ] 运行后端全部测试、覆盖率、Ruff、格式、Mypy 和迁移往返；
- [ ] 运行前端全部测试、Vite 构建及浏览器控制台检查；
- [ ] 更新验证证据，将完成规格移入 `specs/completed/`；
- [ ] 提交 `docs: 验收问题签名告警归集`。
