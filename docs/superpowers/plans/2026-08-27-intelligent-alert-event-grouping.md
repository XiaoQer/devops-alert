# 可解释告警事件聚类与降噪实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本仓库明确禁止子 Agent，必须由当前会话在 `main` 分支顺序执行。

**目标：** 将当前同质告警压缩升级为可解释的事件级聚类，使不同来源、不同症状但属于同一次异常的 Alert 能安全收敛为少量告警事件。

**架构：** 保留 SignalEvent、Alert、AlertGroup 和 Incident 四层边界，以 AlertGroup 表示一次告警事件。入口先继承告警源可信环境，聚类工作器再执行有界候选召回、确定性文本特征、多维评分、事件画像兼容校验和生命周期转换；中置信结果与人工操作使用独立持久关系和不可变决策。

**技术栈：** Python 3.14、FastAPI、Pydantic、SQLAlchemy、Alembic、MySQL 8.4、Pytest、Vue 3、Vite、Vitest、Playwright。

**规格：** `specs/active/intelligent-alert-event-grouping.md`

## 全局约束

- 所有开发直接在 `main` 分支进行，不创建分支、Git worktree 或子 Agent。
- 每项行为变更先写失败测试，再实现最小完整改动，再运行聚焦回归。
- 来源配置是环境的唯一可信事实；不同环境是自动聚类的硬排斥条件。
- 首批观察 30 秒、普通候选回看 15 分钟、恢复观察 5 分钟、迟到容忍 5 分钟、单事件最多 1,000 个成员。
- 七十分及以上且有强锚点自动加入，五十分至六十九分待确认，五十分以下保持独立；前两名差距不足十分不得自动选择。
- 文本相似度最多贡献十五分，不能单独触发跨服务自动聚类。
- 原始负载、Secret、未知字段值和模型隐藏推理不得进入数据库、日志、决策或 API。
- 聚类失败保留 Alert 和持久任务；文本能力、服务目录或 UI 不可用不得阻断接收。
- 本计划不实现核心告警识别、根因分析、自动取证、AI 报告或自动治愈。
- 前端使用中文非技术表达，正文保持 14–16px；内部状态码、UUID、规则码和租约默认隐藏。
- 每个任务只提交列出的文件，始终排除工作区中的 `.codex/`。

---

### 任务 1：告警源可信环境与入口覆盖

**文件：**
- 修改：`backend/src/incident_intelligence/domain/models.py`
- 修改：`backend/src/incident_intelligence/services/alert_sources.py`
- 修改：`backend/src/incident_intelligence/services/signal_intake.py`
- 修改：`backend/src/incident_intelligence/api/schemas/alert_sources.py`
- 修改：`backend/src/incident_intelligence/persistence/models.py`
- 修改：`backend/src/incident_intelligence/persistence/alert_source_repository.py`
- 创建：`backend/migrations/versions/0009_alert_source_environment.py`
- 测试：`backend/tests/unit/domain/test_models.py`
- 测试：`backend/tests/api/test_alert_sources.py`
- 测试：`backend/tests/api/test_dynamic_intake.py`
- 测试：`backend/tests/integration/persistence/test_alert_source_environment_migration.py`
- 测试：`backend/tests/integration/services/test_alert_source_service.py`

**接口：**
- 产生：`EnvironmentCode`，格式为 `^[a-z][a-z0-9-]{0,31}$`。
- 产生：`CreateAlertSourceCommand.environment: EnvironmentCode` 和 `environment_name: str`。
- 产生：`UpdateAlertSourceCommand.environment`、`environment_name` 可选变更字段。
- 产生：`AlertSourceView.environment`、`environment_name`、`environment_configured`。
- 消费：现有动态来源认证与 `SignalCommand`。

- [ ] **步骤 1：编写来源环境和入口继承失败测试**

```python
def test_create_source_requires_environment(client, control_headers):
    response = client.post(
        "/api/v1/alert-sources",
        headers={**control_headers, "Idempotency-Key": "source-env-1"},
        json={"name": "生产 Prometheus", "source_type": "ALERTMANAGER"},
    )
    assert response.status_code == 422


def test_dynamic_intake_uses_source_environment_even_when_payload_conflicts(context):
    source = context.create_source(environment="production", environment_name="生产环境")
    response = context.send_alertmanager(source, labels={"environment": "development"})
    assert response.status_code == 202
    signal = context.latest_signal()
    assert signal.environment == "production"
    assert signal.facts.get("environment") is None
```

- [ ] **步骤 2：运行测试并确认因字段不存在而失败**

```bash
cd backend
.venv/bin/pytest tests/api/test_alert_sources.py tests/api/test_dynamic_intake.py tests/integration/persistence/test_alert_source_environment_migration.py -q
```

预期：创建请求尚不要求环境，`AlertSourceRow` 尚无环境列，冲突载荷仍使用适配器环境。

- [ ] **步骤 3：实现环境类型、迁移、API 和接入覆盖**

```python
EnvironmentCode = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,31}$", max_length=32),
]


class CreateAlertSourceCommand(BaseModel):
    name: SourceName
    source_type: Literal["ALERTMANAGER", "CLOUDEVENTS"]
    environment: EnvironmentCode
    environment_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
```

迁移为 `alert_sources` 增加 `environment`、`environment_name` 和 `environment_configured`；用户来源创建必须提供，历史系统来源回填 `unknown/环境待配置/0`。动态接入在形成持久化命令前使用来源环境覆盖适配器环境，并只记录固定警告码 `source_environment_overrode_payload`，不保存冲突值。

同时把领域 `Environment` 和 SignalEvent、Alert、AlertGroup、Incident、ServiceCatalog 的数据库环境约束从固定枚举改为 `EnvironmentCode` 形态，迁移逐一替换原有 CHECK，确保自定义环境可以贯穿完整数据链路。系统管理来源仍禁止改名、停用和凭据操作，但允许单独配置环境。已经存在活动 Alert 时禁止修改来源环境；来源停用且活动 Alert 归零后才允许切换，历史 SignalEvent、Alert、AlertGroup 和 Incident 永不回填或改写。幂等指纹使用覆盖后的可信环境计算。

- [ ] **步骤 4：运行来源环境全部聚焦测试**

```bash
cd backend
.venv/bin/pytest tests/unit/domain/test_models.py tests/api/test_alert_sources.py tests/api/test_dynamic_intake.py tests/integration/persistence/test_alert_source_environment_migration.py tests/integration/services/test_alert_source_service.py -q
```

预期：全部通过；相同请求重放、版本冲突、来源停用和凭据隔离行为不变。

- [ ] **步骤 5：提交来源环境能力**

```bash
git add backend/src/incident_intelligence/domain/models.py backend/src/incident_intelligence/services/alert_sources.py backend/src/incident_intelligence/services/signal_intake.py backend/src/incident_intelligence/api/schemas/alert_sources.py backend/src/incident_intelligence/persistence/models.py backend/src/incident_intelligence/persistence/alert_source_repository.py backend/migrations/versions/0009_alert_source_environment.py backend/tests
git commit -m "feat: 为告警源增加可信环境"
```

### 任务 2：告警事件持久化模型与无损迁移

**文件：**
- 修改：`backend/src/incident_intelligence/domain/enums.py`
- 修改：`backend/src/incident_intelligence/persistence/models.py`
- 创建：`backend/migrations/versions/0010_alert_event_clustering.py`
- 测试：`backend/tests/integration/persistence/test_alert_event_clustering_migration.py`
- 测试：`backend/tests/integration/persistence/test_constraints.py`

**接口：**
- 产生：`AlertGroupState = FORMING | ACTIVE | OBSERVING | CLOSED`。
- 产生：`AlertGroupMembershipState = AUTO_CONFIRMED | MANUAL_CONFIRMED | PENDING | REMOVED`。
- 产生：`AlertEventProfileRow`、`AlertEventMembershipDecisionRow`、`AlertEventOperationRow`、`AlertEventLifecycleJobRow`。
- 消费：现有 `AlertGroupRow`、`AlertGroupMemberRow`、关联任务和事故关系。

- [ ] **步骤 1：编写迁移往返与约束失败测试**

```python
def test_upgrade_preserves_existing_group_members_and_incident_links(migration_engine):
    seed_alert_grouping_v2_history(migration_engine)
    upgrade_to_head(migration_engine)
    assert scalar(migration_engine, "select count(*) from alert_group_members") == 1
    assert scalar(migration_engine, "select state from alert_groups limit 1") == "ACTIVE"
    assert scalar(migration_engine, "select profile_version from alert_groups limit 1") == 1


def test_pending_membership_cannot_be_active_group_member(session):
    row = make_membership_decision(state="PENDING", selected_group_id=None)
    session.add(row)
    session.flush()
```

- [ ] **步骤 2：运行迁移测试并确认新表和字段缺失**

```bash
cd backend
.venv/bin/pytest tests/integration/persistence/test_alert_event_clustering_migration.py tests/integration/persistence/test_constraints.py -q
```

- [ ] **步骤 3：实现 0010 迁移和 ORM 模型**

```python
class AlertGroupState(StrEnum):
    FORMING = "FORMING"
    ACTIVE = "ACTIVE"
    OBSERVING = "OBSERVING"
    CLOSED = "CLOSED"


class AlertGroupMembershipState(StrEnum):
    AUTO_CONFIRMED = "AUTO_CONFIRMED"
    MANUAL_CONFIRMED = "MANUAL_CONFIRMED"
    PENDING = "PENDING"
    REMOVED = "REMOVED"
```

为 AlertGroup 增加 `profile_version`、`forming_until`、`observing_until`、`closed_at`、`member_limit`、`continuation_group_id` 和 `pending_count`。历史 `RESOLVED` 回填为 `CLOSED`，历史 `ACTIVE` 保持 `ACTIVE`。新增画像、成员判断、人工操作和生命周期任务表；所有 JSON、数量、外键、活动槽和版本字段建立 MySQL CHECK 与索引。

- [ ] **步骤 4：验证升级、降级、ORM 一致性和历史保留**

```bash
cd backend
.venv/bin/pytest tests/integration/persistence/test_alert_event_clustering_migration.py tests/integration/persistence/test_constraints.py tests/integration/persistence/test_alert_group_migration.py -q
```

- [ ] **步骤 5：提交持久化基线**

```bash
git add backend/src/incident_intelligence/domain/enums.py backend/src/incident_intelligence/persistence/models.py backend/migrations/versions/0010_alert_event_clustering.py backend/tests/integration/persistence
git commit -m "feat: 建立告警事件聚类持久化模型"
```

### 任务 3：确定性文本标准化与可选语义接口

**文件：**
- 创建：`backend/src/incident_intelligence/domain/alert_text_similarity.py`
- 创建：`backend/src/incident_intelligence/services/text_similarity.py`
- 测试：`backend/tests/unit/domain/test_alert_text_similarity.py`
- 测试：`backend/tests/unit/services/test_text_similarity.py`

**接口：**
- 产生：`normalize_alert_text(title, summary, alertname, symptom) -> NormalizedAlertText`。
- 产生：`deterministic_similarity(left, right) -> float`，返回 `[0.0, 1.0]`。
- 产生：`TextSimilarityProvider.similarity(left, right) -> SemanticSimilarityResult`。
- 产生：`TextSimilarityService.compare(left, right) -> CombinedTextSimilarity`。

- [ ] **步骤 1：编写动态噪声剥离、中英文相似和安全退化失败测试**

```python
def test_normalization_separates_dynamic_identifiers():
    value = normalize_alert_text(
        "Pod api-7c9f9d8b5-x1 restarting",
        "request 9f7e3d6c-3a00-4e75-a286-8dcd2aaab111 failed at 10.0.0.8",
        "KubePodCrashLooping",
        "restart",
    )
    assert "9f7e3d6c" not in value.normalized
    assert "<uuid>" in value.normalized
    assert "<ip>" in value.normalized


def test_provider_failure_falls_back_to_deterministic_similarity():
    service = TextSimilarityService(provider=FailingProvider())
    result = service.compare(TEXT_A, TEXT_B)
    assert result.provider_status == "FALLBACK"
    assert result.score == result.deterministic_score
```

- [ ] **步骤 2：运行测试并确认模块不存在**

```bash
cd backend
.venv/bin/pytest tests/unit/domain/test_alert_text_similarity.py tests/unit/services/test_text_similarity.py -q
```

- [ ] **步骤 3：实现纯函数与能力协议**

```python
class TextSimilarityProvider(Protocol):
    def similarity(
        self, left: NormalizedAlertText, right: NormalizedAlertText
    ) -> SemanticSimilarityResult: ...


def deterministic_similarity(left: NormalizedAlertText, right: NormalizedAlertText) -> float:
    token_score = weighted_jaccard(left.tokens, right.tokens)
    gram_score = cosine_sparse(left.character_ngrams, right.character_ngrams)
    return round(0.6 * token_score + 0.4 * gram_score, 6)
```

只处理四个标准化安全字段，限制每段长度、词元数量和字符片段数量。Provider 超时、异常、非法分数或未注册均返回固定状态并退化，不写入原始文本或异常详情。

- [ ] **步骤 4：运行单元测试并验证同输入稳定**

```bash
cd backend
.venv/bin/pytest tests/unit/domain/test_alert_text_similarity.py tests/unit/services/test_text_similarity.py -q
```

- [ ] **步骤 5：提交文本特征能力**

```bash
git add backend/src/incident_intelligence/domain/alert_text_similarity.py backend/src/incident_intelligence/services/text_similarity.py backend/tests/unit/domain/test_alert_text_similarity.py backend/tests/unit/services/test_text_similarity.py
git commit -m "feat: 增加可退化文本相似度"
```

### 任务 4：候选评分、事件画像与防链式扩张

**文件：**
- 创建：`backend/src/incident_intelligence/domain/alert_event_clustering.py`
- 创建：`backend/src/incident_intelligence/domain/alert_event_profiles.py`
- 测试：`backend/tests/unit/domain/test_alert_event_clustering.py`
- 测试：`backend/tests/unit/domain/test_alert_event_profiles.py`

**接口：**
- 产生：`score_candidate(context, profile) -> CandidateScore`。
- 产生：`decide_membership(scores) -> MembershipDecision`。
- 产生：`build_event_profile(confirmed_members) -> AlertEventProfile`。
- 消费：任务 3 的 `CombinedTextSimilarity`。

- [ ] **步骤 1：编写阈值、歧义、硬排斥和链式误并失败测试**

```python
def test_high_score_with_strong_anchor_auto_joins():
    score = score_candidate(context_same_entity_and_time(), event_profile())
    decision = decide_membership((score,))
    assert decision.outcome == "AUTO_JOIN"
    assert decision.total_score >= 70


def test_close_top_two_candidates_require_confirmation():
    decision = decide_membership((candidate("agr_a", 78), candidate("agr_b", 73)))
    assert decision.outcome == "PENDING_CONFIRMATION"


def test_text_only_similarity_never_auto_joins():
    score = score_candidate(context_text_only(0.96), unrelated_profile())
    assert score.strong_anchor is False
    assert decide_membership((score,)).outcome == "CREATE_EVENT"
```

- [ ] **步骤 2：运行测试并确认领域模块不存在**

```bash
cd backend
.venv/bin/pytest tests/unit/domain/test_alert_event_clustering.py tests/unit/domain/test_alert_event_profiles.py -q
```

- [ ] **步骤 3：实现评分和画像纯领域逻辑**

```python
class CandidateScore(BaseModel):
    group_id: AlertGroupId
    entity_service_score: int = Field(ge=0, le=35)
    topology_score: int = Field(ge=0, le=25)
    temporal_score: int = Field(ge=0, le=20)
    semantic_score: int = Field(ge=0, le=15)
    history_score: int = Field(ge=0, le=5)
    strong_anchor: bool
    hard_exclusions: tuple[str, ...]


def decide_membership(scores: tuple[CandidateScore, ...]) -> MembershipDecision:
    eligible = tuple(item for item in scores if not item.hard_exclusions)
    ranked = tuple(sorted(eligible, key=lambda item: (-item.total_score, item.group_id)))
    if not ranked or ranked[0].total_score < 50 or not ranked[0].strong_anchor:
        return create_event_decision(ranked)
    if ranked[0].total_score < 70 or (len(ranked) > 1 and ranked[0].total_score - ranked[1].total_score < 10):
        return pending_decision(ranked)
    return auto_join_decision(ranked[0], ranked)
```

画像兼容校验拒绝跨环境、超过两跳和仅匹配边缘成员的扩张。所有排序使用稳定 ID 兜底，解释由固定原因码和分项事实生成。

- [ ] **步骤 4：运行领域测试并覆盖边界分数**

```bash
cd backend
.venv/bin/pytest tests/unit/domain/test_alert_event_clustering.py tests/unit/domain/test_alert_event_profiles.py -q
```

- [ ] **步骤 5：提交聚类领域逻辑**

```bash
git add backend/src/incident_intelligence/domain/alert_event_clustering.py backend/src/incident_intelligence/domain/alert_event_profiles.py backend/tests/unit/domain/test_alert_event_clustering.py backend/tests/unit/domain/test_alert_event_profiles.py
git commit -m "feat: 实现可解释事件聚类评分"
```

### 任务 5：有界候选查询与聚类服务集成

**文件：**
- 修改：`backend/src/incident_intelligence/persistence/alert_group_repository.py`
- 修改：`backend/src/incident_intelligence/services/alert_grouping.py`
- 修改：`backend/src/incident_intelligence/services/alert_grouping_jobs.py`
- 测试：`backend/tests/integration/services/test_alert_grouping_service.py`
- 测试：`backend/tests/integration/services/test_alert_grouping_jobs.py`
- 测试：`backend/tests/integration/test_alert_storm_convergence.py`

**接口：**
- 产生：`AlertGroupRepository.candidate_events(environment, observed_at, entity_key, service, problem_key, limit=50)`。
- 产生：`AlertGroupRepository.save_profile(profile)` 和 `save_membership_decision(decision)`。
- 消费：任务 3、4 的文本比较、评分和画像接口。

- [ ] **步骤 1：编写跨症状聚合、跨来源聚合和安全退化失败测试**

```python
def test_related_database_and_api_alerts_join_one_event(grouping_context):
    first = grouping_context.process(alert="MySQLRowLockWaitActive", service="business-mysql")
    second = grouping_context.process(
        alert="PaymentLatencyHigh",
        service="payment-api",
        dependency_on="business-mysql",
        seconds_after=42,
    )
    assert second.group_id == first.group_id
    assert second.reason_code == "high_confidence_event_match"


def test_different_environment_never_joins(grouping_context):
    production = grouping_context.process(environment="production")
    development = grouping_context.process(environment="development")
    assert development.group_id != production.group_id
```

- [ ] **步骤 2：运行聚焦测试并确认旧 v2 规则无法跨症状归组**

```bash
cd backend
.venv/bin/pytest tests/integration/services/test_alert_grouping_service.py tests/integration/services/test_alert_grouping_jobs.py tests/integration/test_alert_storm_convergence.py -q
```

- [ ] **步骤 3：将编排服务切换到 `alert-event-clustering.v1`**

```python
candidates = repository.candidate_events(
    environment=alert.environment,
    observed_at=alert.last_observed_at,
    entity_key=alert.entity_key,
    service=alert.service,
    problem_key=signature.problem_key,
    limit=50,
)
scores = tuple(self._score(alert, signal, candidate) for candidate in candidates)
decision = decide_membership(scores)
```

自动加入时写正式成员、画像和不可变判断；中置信度只写 PENDING 判断，不增加正式成员计数；保持独立时创建 FORMING 事件。事务内完成任务、成员、画像、判断、审计和必要关联任务更新。

- [ ] **步骤 4：运行服务、任务、风暴和事故关联回归**

```bash
cd backend
.venv/bin/pytest tests/integration/services/test_alert_grouping_service.py tests/integration/services/test_alert_grouping_jobs.py tests/integration/services/test_alert_group_correlation_service.py tests/integration/test_alert_storm_convergence.py tests/integration/test_external_alert_to_incident.py -q
```

- [ ] **步骤 5：提交聚类服务集成**

```bash
git add backend/src/incident_intelligence/persistence/alert_group_repository.py backend/src/incident_intelligence/services/alert_grouping.py backend/src/incident_intelligence/services/alert_grouping_jobs.py backend/tests/integration
git commit -m "feat: 将告警归组升级为事件聚类"
```

### 任务 6：事件生命周期、迟到处理与容量滚动

**文件：**
- 创建：`backend/src/incident_intelligence/domain/alert_event_lifecycle.py`
- 创建：`backend/src/incident_intelligence/services/alert_event_lifecycle.py`
- 创建：`backend/src/incident_intelligence/services/alert_event_lifecycle_jobs.py`
- 创建：`backend/src/incident_intelligence/services/alert_event_lifecycle_runner.py`
- 修改：`backend/src/incident_intelligence/persistence/alert_group_repository.py`
- 修改：`backend/src/incident_intelligence/main.py`
- 修改：`backend/src/incident_intelligence/settings.py`
- 测试：`backend/tests/unit/domain/test_alert_event_lifecycle.py`
- 测试：`backend/tests/integration/services/test_alert_event_lifecycle.py`
- 测试：`backend/tests/unit/services/test_alert_event_lifecycle_runner.py`

**接口：**
- 产生：`decide_lifecycle(context) -> LifecycleDecision`。
- 产生：`AlertEventLifecycleService.process(lease) -> LifecycleResult`。
- 产生：持久 Runner 与现有应用生命周期一起启停。

- [ ] **步骤 1：编写四状态、迟到纠正、复发和容量失败测试**

```python
def test_all_resolved_enters_observing_then_closes():
    assert decide_lifecycle(all_resolved(now=NOW)).state == "OBSERVING"
    assert decide_lifecycle(observing_expired(now=NOW)).state == "CLOSED"


def test_new_trigger_after_closed_creates_recurrence_not_reopen(service):
    closed = service.closed_event(problem_key=PROBLEM_KEY)
    result = service.process_new_alert(problem_key=PROBLEM_KEY, occurred_at=closed.closed_at + MINUTE)
    assert result.group_id != closed.id
    assert result.recurrence_of_problem_key == PROBLEM_KEY


def test_member_1001_rolls_to_continuation_event(service):
    event = service.event_with_members(1_000)
    result = service.process_new_alert(candidate_event=event)
    assert result.group_id != event.id
    assert result.continuation_group_id == event.id
```

- [ ] **步骤 2：运行生命周期测试并确认失败**

```bash
cd backend
.venv/bin/pytest tests/unit/domain/test_alert_event_lifecycle.py tests/integration/services/test_alert_event_lifecycle.py tests/unit/services/test_alert_event_lifecycle_runner.py -q
```

- [ ] **步骤 3：实现持久状态机和定时任务**

```python
class LifecyclePolicy(BaseModel):
    forming_seconds: int = 30
    observing_seconds: int = 300
    allowed_lateness_seconds: int = 300
    member_limit: int = 1_000


def decide_lifecycle(context: LifecycleContext) -> LifecycleDecision:
    if context.active_count > 0:
        return LifecycleDecision(state="ACTIVE", schedule_at=None)
    if context.state in {"FORMING", "ACTIVE"}:
        return LifecycleDecision(state="OBSERVING", schedule_at=context.now + timedelta(minutes=5))
    if context.state == "OBSERVING" and context.observing_until <= context.now:
        return LifecycleDecision(state="CLOSED", schedule_at=None)
    return LifecycleDecision(state=context.state, schedule_at=context.observing_until)
```

迟到补入必须比较 occurred_at、received_at、closed_at 和五分钟容忍；状态纠正写新版本和固定审计。Runner 使用现有租约、最多五次尝试、过期接管和单任务隔离模式。

- [ ] **步骤 4：验证重启接管、恢复观察和 1,001 成员滚动**

```bash
cd backend
.venv/bin/pytest tests/unit/domain/test_alert_event_lifecycle.py tests/integration/services/test_alert_event_lifecycle.py tests/unit/services/test_alert_event_lifecycle_runner.py tests/integration/test_alert_storm_convergence.py -q
```

- [ ] **步骤 5：提交生命周期能力**

```bash
git add backend/src/incident_intelligence/domain/alert_event_lifecycle.py backend/src/incident_intelligence/services/alert_event_lifecycle.py backend/src/incident_intelligence/services/alert_event_lifecycle_jobs.py backend/src/incident_intelligence/services/alert_event_lifecycle_runner.py backend/src/incident_intelligence/persistence/alert_group_repository.py backend/src/incident_intelligence/main.py backend/src/incident_intelligence/settings.py backend/tests
git commit -m "feat: 完成告警事件生命周期"
```

### 任务 7：人工确认、拆分和事件合并 API

**文件：**
- 创建：`backend/src/incident_intelligence/domain/alert_event_operations.py`
- 创建：`backend/src/incident_intelligence/services/alert_event_operations.py`
- 修改：`backend/src/incident_intelligence/api/routes/alert_groups.py`
- 修改：`backend/src/incident_intelligence/api/schemas/alert_groups.py`
- 修改：`backend/src/incident_intelligence/api/dependencies.py`
- 修改：`backend/src/incident_intelligence/main.py`
- 测试：`backend/tests/unit/domain/test_alert_event_operations.py`
- 测试：`backend/tests/integration/services/test_alert_event_operations.py`
- 测试：`backend/tests/api/test_alert_groups.py`

**接口：**
- 产生：`POST /api/v1/alert-groups/{id}/members/{alert_id}/confirm`。
- 产生：`POST /api/v1/alert-groups/{id}/members/split`。
- 产生：`POST /api/v1/alert-groups/{id}/merge`。
- 消费：控制面主体、`Idempotency-Key`、`expected_version` 和任务 2 的操作表。

- [ ] **步骤 1：编写幂等、版本冲突、跨环境拒绝和历史保留失败测试**

```python
def test_merge_rejects_cross_environment_events(client, headers, events):
    response = client.post(
        f"/api/v1/alert-groups/{events.production.id}/merge",
        headers={**headers, "Idempotency-Key": "merge-1"},
        json={"expected_version": 2, "source_group_id": events.development.id, "reason": "同一问题"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "alert_event_environment_conflict"


def test_split_keeps_immutable_membership_history(service):
    result = service.split_members(command())
    assert result.target_group_id != result.source_group_id
    assert service.old_decision_count() == 1
    assert service.removed_membership_count() == 1
```

- [ ] **步骤 2：运行操作测试并确认端点不存在**

```bash
cd backend
.venv/bin/pytest tests/unit/domain/test_alert_event_operations.py tests/integration/services/test_alert_event_operations.py tests/api/test_alert_groups.py -q
```

- [ ] **步骤 3：实现单事务人工操作**

```python
class MergeAlertEventsCommand(BaseModel):
    expected_version: int = Field(ge=1)
    source_group_id: AlertGroupId
    reason: str = Field(min_length=2, max_length=500)


class SplitAlertEventMembersCommand(BaseModel):
    expected_version: int = Field(ge=1)
    alert_ids: tuple[AlertId, ...] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=2, max_length=500)
```

确认、拆分和合并均锁定相关事件，检查版本、环境、终态事故冲突和成员上限；保存旧关系的 REMOVED 状态、新关系、不可变操作、事件画像新版本、审计和后继关联任务。重复幂等键返回第一次结果。

- [ ] **步骤 4：运行人工操作和事故关联回归**

```bash
cd backend
.venv/bin/pytest tests/unit/domain/test_alert_event_operations.py tests/integration/services/test_alert_event_operations.py tests/api/test_alert_groups.py tests/integration/services/test_alert_group_correlation_service.py -q
```

- [ ] **步骤 5：提交人工反馈闭环**

```bash
git add backend/src/incident_intelligence/domain/alert_event_operations.py backend/src/incident_intelligence/services/alert_event_operations.py backend/src/incident_intelligence/api/routes/alert_groups.py backend/src/incident_intelligence/api/schemas/alert_groups.py backend/src/incident_intelligence/api/dependencies.py backend/src/incident_intelligence/main.py backend/tests
git commit -m "feat: 增加告警事件人工纠错"
```

### 任务 8：告警事件读取契约与真实事故判定

**文件：**
- 修改：`backend/src/incident_intelligence/services/alert_group_center.py`
- 修改：`backend/src/incident_intelligence/persistence/alert_center_repository.py`
- 修改：`backend/src/incident_intelligence/api/schemas/alert_groups.py`
- 修改：`backend/src/incident_intelligence/api/routes/alert_groups.py`
- 测试：`backend/tests/integration/services/test_alert_group_center.py`
- 测试：`backend/tests/api/test_alert_groups.py`

**接口：**
- 产生：当前、待确认、历史和原始告警的独立筛选语义。
- 产生：`AlertEventOverview`，包含画像、分项解释、传播时间线、复发次数和真实事故判定。
- 产生：概况指标同时返回 `scope` 与 `window`，禁止混淆当前值和二十四小时值。

- [ ] **步骤 1：编写指标口径、来源友好名称和事故理由失败测试**

```python
def test_summary_separates_current_and_24h_history(center):
    summary = center.summarize("24h", now=NOW)
    assert summary.current.active_events == 0
    assert summary.history.closed_events == 14
    assert summary.history.raw_alerts > 0


def test_unprocessed_group_does_not_claim_pending_incident(center):
    overview = center.get_overview(GROUP_WITHOUT_CORRELATION_JOB)
    assert overview.incident_decision.status == "NOT_EVALUATED"
    assert overview.incident_decision.label == "尚未进行事故判定"
```

- [ ] **步骤 2：运行读取测试并确认现有“待关联事故”语义失败**

```bash
cd backend
.venv/bin/pytest tests/integration/services/test_alert_group_center.py tests/api/test_alert_groups.py -q
```

- [ ] **步骤 3：实现有界事件读取模型**

```python
class IncidentDecisionView(BaseModel):
    status: Literal[
        "NOT_EVALUATED", "PROCESSING", "BELOW_THRESHOLD", "SKIPPED",
        "INCIDENT_CREATED", "INCIDENT_LINKED", "AMBIGUOUS", "FAILED"
    ]
    label: str
    explanation: str
    incident_id: str | None


class AlertEventGroupingExplanation(BaseModel):
    rule_version: str
    total_score: int
    dimensions: tuple[ScoreDimensionView, ...]
    reasons: tuple[str, ...]
```

读取层只查询有界画像、成员判断、友好来源名称、最新真实事故决策和最多二百个时间线节点；大成员集合继续独立分页。不存在任务或决策时返回 NOT_EVALUATED，不推测为处理中。

- [ ] **步骤 4：运行告警中心、事故中心和查询数量回归**

```bash
cd backend
.venv/bin/pytest tests/integration/services/test_alert_group_center.py tests/api/test_alert_groups.py tests/integration/services/test_incident_center.py tests/api/test_incidents.py -q
```

- [ ] **步骤 5：提交读取契约**

```bash
git add backend/src/incident_intelligence/services/alert_group_center.py backend/src/incident_intelligence/persistence/alert_center_repository.py backend/src/incident_intelligence/api/schemas/alert_groups.py backend/src/incident_intelligence/api/routes/alert_groups.py backend/tests
git commit -m "feat: 提供可解释告警事件读取契约"
```

### 任务 9：告警源和告警事件前端改造

**文件：**
- 修改：`frontend/src/api/alertSources.js`
- 修改：`frontend/src/api/alertGroups.js`
- 修改：`frontend/src/composables/useAlertSources.js`
- 修改：`frontend/src/composables/useAlertGroupCenter.js`
- 修改：`frontend/src/presentation/alertSourceView.js`
- 修改：`frontend/src/presentation/alertGroupView.js`
- 修改：`frontend/src/components/AlertSourceDialog.vue`
- 修改：`frontend/src/components/AlertSourceManager.vue`
- 修改：`frontend/src/components/AlertGroupCenter.vue`
- 修改：`frontend/src/styles.css`
- 测试：对应目录下现有 `*.test.js`

**接口：**
- 消费：任务 1 的来源环境契约和任务 8 的事件读取契约。
- 产生：`当前事件 | 待确认 | 历史事件 | 原始告警` 四个一级视图。
- 产生：确认成员、拆分成员和合并事件交互，写成功后重新读取真实事实。

- [ ] **步骤 1：编写来源环境、指标口径和事件解释交互失败测试**

```javascript
it("创建告警源时必须选择环境", async () => {
  render(AlertSourceDialog, { props: { open: true } });
  await fireEvent.click(screen.getByRole("button", { name: "创建并生成凭据" }));
  expect(screen.getByText("请选择告警源所属环境")).toBeTruthy();
});

it("默认只展示当前事件并解释真实事故判定", async () => {
  render(AlertGroupCenter);
  expect(await screen.findByRole("tab", { name: "当前事件" })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByText("尚未进行事故判定")).toBeTruthy();
  expect(screen.queryByText("待关联事故")).toBeNull();
});
```

- [ ] **步骤 2：运行前端测试并确认新视图和字段不存在**

```bash
cd frontend
npm test -- --run
```

- [ ] **步骤 3：实现非技术化告警事件界面**

```javascript
export function toAlertEventView(group) {
  return {
    id: group.id,
    title: group.title,
    state: eventStateLabels[group.state],
    environment: group.environment_name,
    compression: `${group.signal_count} 条信号 → ${group.alert_count} 条告警 → 1 个事件`,
    incidentDecision: group.incident_decision.label,
    groupingReasons: group.grouping_explanation.reasons,
  };
}
```

事件详情按“发生了什么、是否持续、影响、归集原因、传播时间线、待确认、原始告警、事故判定”排列。主界面只显示友好来源名；技术规则和 ID 放入折叠审计区。正文 14–16px，状态同时使用文字和图形，不只依赖颜色。

- [ ] **步骤 4：运行 Vitest、构建和 Sites Worker 测试**

```bash
cd frontend
npm test -- --run
npm run build
npm run test:sites
```

- [ ] **步骤 5：提交前端闭环**

```bash
git add frontend/src frontend/tests
git commit -m "feat: 重构告警事件降噪界面"
```

### 任务 10：真实容量回放、迁移工具与文档验收

**文件：**
- 修改：`backend/src/incident_intelligence/services/alert_regrouping.py`
- 修改：`backend/tests/integration/test_alert_storm_convergence.py`
- 创建：`backend/tests/integration/test_alert_event_cascade_convergence.py`
- 修改：`docs/architecture.md`
- 修改：`docs/current-state.md`
- 创建：`docs/verification/2026-08-27-intelligent-alert-event-grouping.md`
- 移动：`specs/active/intelligent-alert-event-grouping.md` → `specs/completed/intelligent-alert-event-grouping.md`

**接口：**
- 产生：每批最多一百个活动旧组的幂等迁移入口。
- 产生：一百至一千条跨症状、跨来源告警的真实 MySQL 回放证据。
- 消费：任务 1–9 的全部能力。

- [ ] **步骤 1：编写端到端容量与重放失败测试**

```python
def test_1000_alert_cascade_converges_without_fact_loss(real_mysql_context):
    result = real_mysql_context.ingest_cascade(
        count=1_000,
        services=("business-mysql", "payment-api", "checkout-api"),
        symptoms=("lock_wait", "latency", "error_rate"),
        concurrency=10,
    )
    assert result.signal_events == 1_000
    assert result.alerts == 1_000
    assert result.confirmed_members + result.pending_members == 1_000
    assert result.primary_event_count <= 3
    assert result.active_jobs == 0
    replay = real_mysql_context.replay_same_payloads()
    assert replay.signal_events == 1_000
```

- [ ] **步骤 2：运行端到端测试并确认旧规则产生多个同质组**

```bash
cd backend
.venv/bin/pytest tests/integration/test_alert_event_cascade_convergence.py tests/integration/test_alert_storm_convergence.py -q
```

- [ ] **步骤 3：实现有界活动组迁移并更新事实文档**

```python
class AlertEventRegroupResult(BaseModel):
    examined_groups: int = Field(ge=0, le=100)
    migrated_members: int = Field(ge=0)
    created_pending_decisions: int = Field(ge=0)
    unchanged_groups: int = Field(ge=0)
    replayed: bool
```

迁移只处理活动、未绑定冲突事故的旧规则组；按稳定 ID、每批最多一百个执行，保留旧决策和审计。`docs/current-state.md` 只记录实际通过的能力和数字，并继续明确核心告警识别、根因定位和治愈尚未实现。

- [ ] **步骤 4：运行统一验证和真实浏览器旅程**

```bash
./scripts/verify-backend.sh
cd frontend
npm test -- --run
npm run build
npm run test:sites
```

随后在真实 MySQL 与本地前后端上验证：配置来源环境、接收跨症状告警、查看事件压缩、检查归组分数、确认待确认成员、拆分、合并、恢复观察、历史复发和事故真实判定；记录浏览器控制台、键盘操作和二倍缩放结果。

- [ ] **步骤 5：完成规格并提交验收证据**

```bash
git add backend/src/incident_intelligence/services/alert_regrouping.py backend/tests docs specs
git commit -m "docs: 验收告警事件聚类降噪"
```

## 执行检查点

- 任务 1–2 完成后：评审来源环境迁移和数据库模型，确认历史数据无损。
- 任务 3–4 完成后：评审文本处理、评分阈值和防链式误并纯领域测试。
- 任务 5–6 完成后：评审真实 MySQL 聚类结果、生命周期和服务重启恢复。
- 任务 7–8 完成后：评审人工纠错、读取契约和事故判定语义。
- 任务 9 完成后：在浏览器检查信息层级、可读性和失败状态。
- 任务 10 完成后：运行统一验证，核对规格全部验收条件，再移动规格状态。
