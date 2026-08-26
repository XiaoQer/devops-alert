# 面向实体的告警接入与服务识别实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 让缺少 `service` 的 Alertmanager 告警可靠进入告警中心，以真实资源实体归组，并在可选 Kubernetes 只读解析器确认服务后重新进入现有事故关联链路。

**架构：** 接入事务只做协议校验、实体提取和不可变事实保存，不访问 Kubernetes。Alert 保存可变的服务识别投影；持久解析任务异步读取 Pod 白名单标签，成功后更新 Alert 并重新调度归组。AlertGroup 使用实体键代替服务作为相似性主键，事故关联仍只接受已确认服务。

**技术栈：** Python 3.13、FastAPI、Pydantic、SQLAlchemy、Alembic、MySQL 8.4、Vue 3、Vitest、Pytest。

**规格：** `specs/active/entity-aware-alert-intake.md`；详细设计：`docs/superpowers/specs/2026-08-26-entity-aware-alert-intake-design.md`。

## 全局约束

- 所有变更直接提交到 `main`，不创建分支或 worktree，不使用子 Agent。
- 严格测试先行：每个生产行为先观察对应测试按预期失败，再写最小实现。
- 不修改 PrometheusRule、Alertmanager 配置或 Kubernetes 资源。
- 不根据 Pod 名称、`job`、`namespace`、`node` 或 `cluster` 猜测业务服务。
- Kubernetes 解析器只读 Pod 元数据，只提取 `app.kubernetes.io/name` 和 `app`。
- 缺少服务、解析失败或解析器关闭不得阻断告警接入和人工处置。
- SignalEvent 保持不可变，后续解析只更新 Alert 当前投影并追加任务与审计。
- Secret、原始 Webhook、完整 Pod 对象和 Kubernetes 响应正文不得进入数据库、日志或 API。
- CloudEvents 和人工报告继续要求服务，本阶段不改变其输入契约。
- `scenario_id`、`scenario_version`、`experiment_id`、注入动作和标准答案继续被递归拒绝。

---

### 任务 1：建立实体身份与服务识别领域契约

**文件：**
- 创建：`backend/src/incident_intelligence/domain/entities.py`
- 修改：`backend/src/incident_intelligence/domain/signal_intake.py`
- 修改：`backend/src/incident_intelligence/domain/models.py`
- 测试：`backend/tests/unit/domain/test_entities.py`
- 测试：`backend/tests/unit/domain/test_signal_intake.py`

**接口：**
- 产生：`EntityIdentity`、`EntityType`、`ServiceResolutionStatus`、`ServiceResolutionSource`、`ResolutionConfidence`、`ResolutionReasonCode`。
- 产生：`derive_entity_identity(labels: Mapping[str, str], alert_id: str | None = None) -> EntityIdentity`。
- 产生：SignalCommand 的 `service: ServiceName | None`、`entity` 和服务识别字段。
- 消费：后续适配器、持久化、归组、API 和解析任务共享这些字段。

- [ ] **步骤 1：编写实体优先级失败测试**

```python
def test_pod_identity_is_pending_without_service() -> None:
    identity = derive_entity_identity({"namespace": "devops-platform", "pod": "demo-0"})
    assert identity.entity_type == "POD"
    assert identity.display_name == "devops-platform/demo-0"
    assert identity.service is None
    assert identity.service_resolution_status == "PENDING"

def test_explicit_service_has_priority_over_resource_labels() -> None:
    identity = derive_entity_identity(
        {"service": "payment-api", "namespace": "prod", "pod": "payment-0"}
    )
    assert identity.entity_type == "SERVICE"
    assert identity.service == "payment-api"
    assert identity.service_resolution_source == "ALERT_LABEL"
```

- [ ] **步骤 2：运行聚焦测试并确认因模块不存在而失败**

运行：`cd backend && .venv/bin/pytest tests/unit/domain/test_entities.py -q`

预期：收集失败，提示 `incident_intelligence.domain.entities` 不存在。

- [ ] **步骤 3：实现最小不可变领域模型和稳定实体键**

```python
class EntityIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    entity_type: EntityType
    entity_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    display_name: str = Field(min_length=1, max_length=257)
    service: ServiceName | None = None
    service_resolution_status: ServiceResolutionStatus
    service_resolution_source: ServiceResolutionSource | None = None
    service_resolution_confidence: ResolutionConfidence | None = None
    service_resolution_reason_codes: tuple[ResolutionReasonCode, ...] = ()
```

实体键固定为 `sha256("entity.v1\\0{type}\\0{normalized parts}")`；SERVICE、POD、NODE、JOB、INSTANCE、CLUSTER 和 UNKNOWN 分别使用规格定义的真实标签组合。

- [ ] **步骤 4：运行实体测试并确认通过**

运行：`cd backend && .venv/bin/pytest tests/unit/domain/test_entities.py -q`

预期：全部通过。

- [ ] **步骤 5：编写 SignalCommand 和 Alert 可空服务投影失败测试**

```python
def test_projection_preserves_pending_pod_identity() -> None:
    command = make_command(
        service=None,
        entity_type="POD",
        entity_display_name="devops-platform/demo-0",
        service_resolution_status="PENDING",
    )
    decision = decide_alert_projection(None, command, ALT_ID, SIG_ID, NOW)
    assert decision.alert is not None
    assert decision.alert.service is None
    assert decision.alert.entity_type == "POD"
```

- [ ] **步骤 6：运行测试并确认现有必填 ServiceName 校验导致失败**

运行：`cd backend && .venv/bin/pytest tests/unit/domain/test_signal_intake.py -q`

预期：`service` 的 Pydantic 校验失败或 Alert 缺少实体字段。

- [ ] **步骤 7：扩展 SignalCommand、SignalEvent 和 Alert 并保持投影版本语义**

所有新字段显式进入 `_project_command`；解析初态随真实新事件更新，但已由高可信解析器确认的服务不得被后续无服务 firing 事件降级。

- [ ] **步骤 8：运行领域回归并提交**

运行：`cd backend && .venv/bin/pytest tests/unit/domain/test_entities.py tests/unit/domain/test_signal_intake.py tests/unit/domain/test_models.py -q`

提交：`git commit -am "feat: 建立告警实体身份模型"`

---

### 任务 2：放宽 Alertmanager 接入并合并公共标签

**文件：**
- 修改：`backend/src/incident_intelligence/adapters/alertmanager.py`
- 测试：`backend/tests/unit/adapters/test_alertmanager.py`
- 测试：`backend/tests/api/test_alertmanager_intake.py`
- 测试：`backend/tests/api/test_dynamic_intake.py`

**接口：**
- 消费：`derive_entity_identity(labels)`。
- 产生：无服务告警的 SignalCommand；动态来源和兼容入口行为一致。

- [ ] **步骤 1：编写无服务、混合批次和 commonLabels 失败测试**

```python
def test_pod_alert_without_service_is_normalized() -> None:
    payload = webhook(service=None, labels={"namespace": "devops-platform", "pod": "demo-0"})
    command = to_signal_commands(AlertmanagerWebhook.model_validate(payload), NOW)[0]
    assert command.service is None
    assert command.entity_type == "POD"

def test_per_alert_labels_override_common_labels() -> None:
    payload = webhook(common_labels={"service": "shared"}, labels={"service": "specific"})
    assert normalize(payload).service == "specific"
```

API 测试发送一条有服务和一条无服务的同批 Webhook，断言 202、`accepted_count == 2` 且数据库存在两条 Alert。

- [ ] **步骤 2：运行测试并确认 `missing_service` 失败**

运行：`cd backend && .venv/bin/pytest tests/unit/adapters/test_alertmanager.py tests/api/test_alertmanager_intake.py tests/api/test_dynamic_intake.py -q`

- [ ] **步骤 3：实现公共标签合并和实体提取**

`to_signal_commands` 为每条告警构造 `{**webhook.common_labels, **alert.labels}`；删除 `missing_service` 分支；标题、时间、容量、严格 Schema 和禁止身份规则保持不变。

- [ ] **步骤 4：验证适配器与 HTTP 入口并提交**

运行：`cd backend && .venv/bin/pytest tests/unit/adapters/test_alertmanager.py tests/api/test_alertmanager_intake.py tests/api/test_dynamic_intake.py -q`

提交：`git commit -am "feat: 接收缺少服务标签的告警"`

---

### 任务 3：迁移 MySQL 并持久化实体投影

**文件：**
- 创建：`backend/migrations/versions/0007_entity_aware_alerts.py`
- 修改：`backend/src/incident_intelligence/persistence/models.py`
- 修改：`backend/src/incident_intelligence/persistence/repositories.py`
- 修改：`backend/src/incident_intelligence/services/signal_intake.py`
- 测试：`backend/tests/integration/persistence/test_entity_aware_migration.py`
- 测试：`backend/tests/integration/services/test_signal_intake_service.py`

**接口：**
- 产生：SignalEventRow、AlertRow、AlertGroupRow 的可空 `service` 与实体字段。
- 产生：`entity_resolution_jobs` 表和 ORM 行模型骨架。
- 消费：任务 1 的领域字段。

- [ ] **步骤 1：编写迁移升级、回填和保护性降级失败测试**

测试从 0006 插入一套既有服务数据，升级后断言：

```python
assert alert.service == "payment-api"
assert alert.entity_type == "SERVICE"
assert alert.service_resolution_status == "RESOLVED"
assert alert.service_resolution_source == "ALERT_LABEL"
assert len(alert.entity_key) == 64
```

再插入空服务 POD 告警并断言迁移允许保存；存在空服务时 downgrade 必须抛出包含 `entity_aware_downgrade_requires_resolved_services` 的错误。

- [ ] **步骤 2：运行迁移测试并确认 0007 不存在而失败**

运行：`cd backend && .venv/bin/pytest tests/integration/persistence/test_entity_aware_migration.py -q`

- [ ] **步骤 3：实现 0007 迁移和 ORM 字段**

新增实体类型、实体键、显示名、解析状态、来源、可信度、原因码和解析时间；先回填再放宽 service。`entity_resolution_jobs` 使用唯一活动槽、租约、尝试次数、固定错误码和目标 Alert 版本。

- [ ] **步骤 4：验证迁移与 ORM 一致性**

运行：`cd backend && .venv/bin/pytest tests/integration/persistence/test_entity_aware_migration.py tests/integration/persistence/test_initial_migration.py tests/integration/persistence/test_constraints.py -q`

- [ ] **步骤 5：编写接入原子持久化失败测试**

断言 PENDING POD Alert 与 SignalEvent 在同一事务写入，并只生成一个活动解析任务；精确重放不新增任务；第二条命令失败整批零写入。

- [ ] **步骤 6：实现仓储映射和解析任务调度**

`SignalIntakeService` 仅对 PENDING Alert 调度任务；任务只保存 Alert ID、版本、实体类型、实体键、namespace 和 pod 等白名单线索，不保存 Webhook。

- [ ] **步骤 7：运行接入服务回归并提交**

运行：`cd backend && .venv/bin/pytest tests/integration/services/test_signal_intake_service.py tests/integration/services/test_source_isolation.py -q`

提交：`git commit -am "feat: 持久化告警实体与解析任务"`

---

### 任务 4：按实体归组并安全跳过服务型事故关联

**文件：**
- 修改：`backend/src/incident_intelligence/domain/alert_grouping.py`
- 修改：`backend/src/incident_intelligence/domain/alert_group_correlation.py`
- 修改：`backend/src/incident_intelligence/persistence/alert_group_repository.py`
- 修改：`backend/src/incident_intelligence/services/alert_grouping.py`
- 修改：`backend/src/incident_intelligence/services/alert_group_correlation.py`
- 测试：`backend/tests/unit/domain/test_alert_grouping.py`
- 测试：`backend/tests/unit/domain/test_alert_group_correlation.py`
- 测试：`backend/tests/integration/services/test_alert_grouping_service.py`
- 测试：`backend/tests/integration/services/test_alert_group_correlation_service.py`
- 测试：`backend/tests/integration/test_alert_storm_convergence.py`

**接口：**
- GroupingContext 和 AlertGroupCandidate 使用 `entity_type + entity_key`，service 可空。
- 产生固定原因码 `same_entity_environment_symptom_window`、`service_resolution_pending`、`service_not_applicable`。

- [ ] **步骤 1：编写相同 Pod 无服务归组失败测试**

```python
def test_same_pending_pod_joins_group_without_catalog() -> None:
    decision = decide_alert_group(pending_pod_context(candidate_count=1))
    assert decision.action == "JOIN_GROUP"
    assert decision.reason_codes == ("same_entity_environment_symptom_window",)
```

另测不同 Pod 不合并、Node 告警按 Node 合并、已有 SERVICE 行为保持。

- [ ] **步骤 2：运行领域测试并确认现有服务目录门槛导致失败**

运行：`cd backend && .venv/bin/pytest tests/unit/domain/test_alert_grouping.py tests/unit/domain/test_alert_group_correlation.py -q`

- [ ] **步骤 3：把归组主键改为实体键**

服务目录仅影响 SERVICE 实体；POD、NODE、JOB 等无需目录即可按同实体归组。候选查询改为 `entity_key + environment + symptom`。

- [ ] **步骤 4：编写未解析组关联跳过失败测试**

处理 PENDING POD 组后断言不创建 Incident，决策 outcome 为 `SKIPPED`，reason code 为 `service_resolution_pending`，facts 只包含实体类型、环境、严重度、症状和成员数。

- [ ] **步骤 5：实现可解释安全跳过**

关联服务在读取服务目录前检查 `group.service` 和解析状态；空服务禁止调用 `find_service_identity`，禁止构造 IncidentRow。

- [ ] **步骤 6：运行归组、关联和 100 条风暴回归并提交**

运行：`cd backend && .venv/bin/pytest tests/unit/domain/test_alert_grouping.py tests/unit/domain/test_alert_group_correlation.py tests/integration/services/test_alert_grouping_service.py tests/integration/services/test_alert_group_correlation_service.py tests/integration/test_alert_storm_convergence.py -q`

提交：`git commit -am "feat: 按真实实体归组无服务告警"`

---

### 任务 5：实现持久服务解析任务和可选 Kubernetes Pod 解析器

**文件：**
- 修改：`backend/pyproject.toml`
- 修改：`backend/requirements.lock`
- 创建：`backend/src/incident_intelligence/services/entity_resolution.py`
- 创建：`backend/src/incident_intelligence/services/entity_resolution_jobs.py`
- 创建：`backend/src/incident_intelligence/services/entity_resolution_runner.py`
- 创建：`backend/src/incident_intelligence/integrations/kubernetes.py`
- 修改：`backend/src/incident_intelligence/persistence/unit_of_work.py`
- 修改：`backend/src/incident_intelligence/persistence/repositories.py`
- 修改：`backend/src/incident_intelligence/settings.py`
- 修改：`backend/src/incident_intelligence/main.py`
- 测试：`backend/tests/unit/integrations/test_kubernetes.py`
- 测试：`backend/tests/integration/services/test_entity_resolution.py`
- 测试：`backend/tests/unit/services/test_entity_resolution_runner.py`
- 测试：`backend/tests/unit/test_settings.py`

**接口：**
- `PodMetadataReader.read_labels(namespace: str, pod: str) -> Mapping[str, str]`。
- `EntityResolutionService.process(lease: EntityResolutionLease) -> EntityResolutionResult`。
- 设置：`entity_resolution_runner_enabled=False` 默认为关闭；显式启用时要求 Kubernetes API 配置可用。
- 使用官方 `kubernetes>=36.0.3,<37` 客户端；优先加载集群内 ServiceAccount，未处于集群内时加载当前 kubeconfig。

- [ ] **步骤 1：编写 Pod 白名单标签解析失败测试**

```python
def test_reader_returns_only_service_candidate_labels() -> None:
    labels = reader.extract_labels({
        "app.kubernetes.io/name": "aegis-springboot-demo",
        "app": "fallback",
        "secret-like": "must-not-leave-client",
    })
    assert labels == {
        "app.kubernetes.io/name": "aegis-springboot-demo",
        "app": "fallback",
    }
```

同时覆盖 404、403、超时和非法响应映射为固定异常码，响应正文不进入异常文本。

- [ ] **步骤 2：运行测试并确认集成模块不存在而失败**

运行：`cd backend && .venv/bin/pytest tests/unit/integrations/test_kubernetes.py -q`

- [ ] **步骤 3：锁定官方 Kubernetes 客户端并实现可注入的只读 PodMetadataReader**

把 `kubernetes>=36.0.3,<37` 加入生产依赖并重新生成锁文件。生产实现使用官方 `CoreV1Api.read_namespaced_pod`；API 对象通过构造函数注入，测试不访问真实集群。配置加载优先 `load_incluster_config()`，仅在确认不是集群内环境时调用 `load_kube_config()`；凭据由官方客户端持有，平台代码不读取或记录 Token。

- [ ] **步骤 4：编写解析成功、失败和过期任务测试**

成功场景断言优先采用 `app.kubernetes.io/name`，Alert 变为 RESOLVED/HIGH/KUBERNETES_POD_LABEL，版本增加，生成新归组任务和有界审计；过期版本返回 SUPERSEDED；缺失标签最终变为 UNRESOLVED；短暂失败保持 PENDING 并重试。

- [ ] **步骤 5：实现任务领取、处理、租约接管和 Runner**

复用现有 Runner 的停止事件和逐任务隔离模式。解析更新与重新归组处于一个 MySQL 事务；不修改 SignalEvent。

- [ ] **步骤 6：把 Runner 作为默认关闭的可选组件接入应用生命周期**

关闭时不构造 Kubernetes 客户端、不启动任务；开启但配置错误时应用就绪状态明确失败，错误不回显凭据。

- [ ] **步骤 7：运行解析与生命周期测试并提交**

运行：`cd backend && .venv/bin/pytest tests/unit/integrations/test_kubernetes.py tests/integration/services/test_entity_resolution.py tests/unit/services/test_entity_resolution_runner.py tests/unit/test_settings.py tests/api/test_health.py -q`

提交：`git commit -am "feat: 增加可选 Kubernetes 服务解析器"`

---

### 任务 6：在告警 API 和前端展示实体识别状态

**文件：**
- 修改：`backend/src/incident_intelligence/services/alert_center.py`
- 修改：`backend/src/incident_intelligence/services/alert_group_center.py`
- 修改：`backend/src/incident_intelligence/persistence/alert_center_repository.py`
- 修改：`backend/src/incident_intelligence/api/schemas/alerts.py`
- 修改：`backend/src/incident_intelligence/api/schemas/alert_groups.py`
- 修改：`frontend/src/presentation/alertView.js`
- 修改：`frontend/src/presentation/alertGroupView.js`
- 修改：`frontend/src/components/AlertCenter.vue`
- 修改：`frontend/src/components/AlertGroupCenter.vue`
- 修改：`frontend/src/test-fixtures/alerts.js`
- 测试：`backend/tests/api/test_alerts.py`
- 测试：`backend/tests/api/test_alert_groups.py`
- 测试：`frontend/src/presentation/alertView.test.js`
- 测试：`frontend/src/components/AlertCenter.test.js`
- 测试：`frontend/src/components/AlertGroupCenter.test.js`

**接口：**
- Alert API 增加 `entity` 和 `service_resolution` 对象，`service` 改为 `string | null`。
- 前端产生 `entityLabel`、`serviceLabel`、`resolutionLabel`、`resolutionTone`。

- [ ] **步骤 1：编写 API 可空服务和实体展示失败测试**

```python
assert body["service"] is None
assert body["entity"] == {
    "type": "POD",
    "display_name": "devops-platform/demo-0",
}
assert body["service_resolution"]["status"] == "PENDING"
```

API 不返回内部 entity_key、Kubernetes 响应或凭据。

- [ ] **步骤 2：运行 API 测试并确认响应缺少新字段而失败**

运行：`cd backend && .venv/bin/pytest tests/api/test_alerts.py tests/api/test_alert_groups.py -q`

- [ ] **步骤 3：实现白名单响应和筛选**

新增 `service_resolution_status` 查询参数；搜索覆盖实体显示名；服务筛选仅匹配非空已识别服务。

- [ ] **步骤 4：编写前端中文展示失败测试**

```javascript
expect(toAlertListItem(pendingPod).service).toBe("正在识别")
expect(toAlertListItem(pendingPod).entityLabel).toBe("Pod · devops-platform/demo-0")
expect(wrapper.text()).not.toContain("unknown-service")
```

分别覆盖 RESOLVED、PENDING、UNRESOLVED 和 NOT_APPLICABLE。

- [ ] **步骤 5：运行前端测试并确认旧视图直接显示空服务而失败**

运行：`cd frontend && npm test -- src/presentation/alertView.test.js src/components/AlertCenter.test.js src/components/AlertGroupCenter.test.js`

- [ ] **步骤 6：实现中文证据化展示**

列表优先展示服务，未解析时展示主要实体；详情明确展示识别状态与依据；Node/Cluster 显示“不适用”，不提示补 service。

- [ ] **步骤 7：运行前后端聚焦测试并提交**

运行：`cd backend && .venv/bin/pytest tests/api/test_alerts.py tests/api/test_alert_groups.py -q`

运行：`cd frontend && npm test -- src/presentation/alertView.test.js src/components/AlertCenter.test.js src/components/AlertGroupCenter.test.js`

提交：`git commit -am "feat: 展示告警实体识别状态"`

---

### 任务 7：真实集群联调、统一验收和文档收口

**文件：**
- 修改：`docs/current-state.md`
- 修改：`docs/architecture.md`
- 修改：`README.md`
- 修改：`specs/active/entity-aware-alert-intake.md`
- 创建：`docs/verification/2026-08-26-entity-aware-alert-intake.md`
- 最终移动：`specs/active/entity-aware-alert-intake.md` → `specs/completed/entity-aware-alert-intake.md`

**接口：**
- 消费：前六项任务的最终行为。
- 产生：可重复执行的验收证据和准确的当前能力说明。

- [ ] **步骤 1：运行完整后端验证**

运行仓库现有统一后端验证命令；必须包含全部 Pytest、覆盖率、Ruff、格式、Mypy 和 MySQL 迁移往返。任何失败先写最小回归测试再修复。

- [ ] **步骤 2：运行完整前端验证**

运行：`cd frontend && npm test && npm run build && npm run test:sites`

预期：全部通过，构建产物不包含内部 Token、Kubernetes 凭据或禁止实验身份。

- [ ] **步骤 3：使用真实 Alertmanager 载荷进行只读联调**

从当前 Prometheus 查询一条真实 `KubePodNotReady` 标签组合，构造正常 Alertmanager v4 Webhook 发送到本项目动态来源。只读解析器读取对应 Pod 标签，不修改集群。验证：

```text
接入 HTTP 202
Alert.entity_type = POD
Alert.service = aegis-springboot-demo
Alert.service_resolution_source = KUBERNETES_POD_LABEL
SignalEvent.service 保持 null
告警中心显示真实 Pod 和解析依据
```

- [ ] **步骤 4：验证安全退化**

关闭解析器后再次发送另一条无服务告警，验证接入仍为 202、告警显示“正在识别”、不创建虚假 Incident；恢复告警使用同 fingerprint 收敛到同一 Alert。

- [ ] **步骤 5：更新当前状态和验证记录**

文档只记录实际通过的数量、命令结果、真实联调事实和已知缺口；可选解析器不得描述为默认启用。

- [ ] **步骤 6：完成规格并提交**

将验收条件逐条补充证据，状态改为“已验收”，移动到 completed。

提交：`git commit -am "docs: 验收面向实体的告警接入"`

- [ ] **步骤 7：最终工作区核对**

运行：`git status --short --branch` 和 `git log -8 --oneline`。

预期：仅保留用户原有 `.codex/` 未跟踪内容；所有项目变更均已提交到 main。
