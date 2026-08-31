# Alert 生命周期投影实施计划

> 执行要求：实施时必须使用 superpowers:executing-plans，按任务逐项执行和验收。本项目禁止子 Agent、功能分支和 Git worktree，所有改动直接在 main 分支顺序完成。

目标：在不可变 SignalEvent 之上建立独立 Alert 生命周期投影，使同一轮告警的 firing 与 resolved 始终表现为一条告警记录，并让告警列表、详情和趋势图全部读取该投影。

架构：接入适配器把不同来源标准化为包含 alert_name、source_alert_key 和 episode_started_at 的 SignalCommand。接入事务先保存 SignalEvent，再以 (alert_source_id, source_alert_key, episode_started_at) 锁定并更新 Alert，同时保存信号与告警的关联结果。读侧 API 和前端只读取 Alert；SignalEvent 继续作为审计事实保留。

技术栈：Python 3.12、FastAPI、Pydantic、SQLAlchemy、Alembic、MySQL、pytest；Vue 3、Vite、Vitest。

---

## 实施边界与关键决策

- 本轮只完成 SignalEvent → Alert，不创建或关联 Incident，不引入取证和 AI。
- 新领域对象叫 Alert，新物理表叫 alert_lifecycles。旧 alerts 表已有历史事故外键，本次不复用、不删除，避免继续耦合旧模型。
- SignalEvent 不可变；仅为今后的新事件持久化标准化身份字段。历史缺失字段不回写，由回填程序保守重建。
- Alertmanager 的告警名称只能来自 labels.alertname，不能用摘要或“未命名告警”代替。
- Watchdog 只保留 SignalEvent 审计，不创建 Alert。
- 趋势按 Alert.first_received_at 计数；resolved 只更新状态，不增加数量。
- SignalEvent、Alert、接入结果和审计必须同事务提交或回滚。

## 任务 1：统一接入语义

涉及文件：

- 修改 backend/src/incident_intelligence/domain/models.py
- 修改 backend/src/incident_intelligence/domain/signal_intake.py
- 修改 backend/src/incident_intelligence/adapters/alertmanager.py
- 修改 backend/src/incident_intelligence/adapters/cloudevents.py
- 修改 backend/tests/unit/adapters/test_alertmanager.py
- 修改 backend/tests/unit/adapters/test_cloudevents.py

- [ ] 步骤 1：先写 Alertmanager 失败测试

覆盖：

~~~python
def test_alert_name_must_come_from_alertname():
    command = parse_alertmanager_payload(
        source=source(),
        payload=payload(
            labels={"alertname": "MySQLRowLockWaitActive"},
            annotations={"summary": "MySQL 行锁等待", "description": "等待事务超过阈值"},
        ),
    )[0]
    assert command.alert_name == "MySQLRowLockWaitActive"
    assert command.summary == "MySQL 行锁等待"
    assert command.description == "等待事务超过阈值"


def test_missing_alertname_is_rejected_even_when_summary_exists():
    with pytest.raises(AdapterValidationError, match="missing_alert_name"):
        parse_alertmanager_payload(
            source=source(),
            payload=payload(labels={}, annotations={"summary": "数据库告警"}),
        )
~~~

- [ ] 步骤 2：先写 CloudEvents 失败测试

显式 data.alert_name 优先；缺失时使用 CloudEvents type，不能使用 summary 生成名称。

- [ ] 步骤 3：运行测试，确认失败

~~~bash
cd backend
.venv/bin/pytest tests/unit/adapters/test_alertmanager.py tests/unit/adapters/test_cloudevents.py -q
~~~

- [ ] 步骤 4：实现 SignalCommand 新字段和映射

SignalCommand 增加：

~~~python
alert_name: AlertName
summary: str = Field(default="", max_length=500)
description: str = Field(default="", max_length=2_000)
~~~

Alertmanager 映射固定为：

~~~python
alert_name = labels.get("alertname")
if not alert_name:
    raise AdapterValidationError("missing_alert_name")

command = SignalCommand(
    alert_name=alert_name,
    title=alert_name,
    summary=annotations.get("summary", ""),
    description=annotations.get("description", ""),
    # 其余身份、状态和资源字段沿用现有映射
)
~~~

CloudEvents 增加可选 data.alert_name 和 data.description，alert_name 使用 data.alert_name or event.type。title 只作旧 SignalEvent 兼容字段，不参与 Alert 身份。

- [ ] 步骤 5：验证并提交

~~~bash
cd backend
.venv/bin/pytest tests/unit/adapters/test_alertmanager.py tests/unit/adapters/test_cloudevents.py -q
git add backend/src/incident_intelligence/domain backend/src/incident_intelligence/adapters backend/tests/unit/adapters
git commit -m "feat: 统一接入告警语义"
~~~

## 任务 2：建立 Alert 生命周期存储

涉及文件：

- 新建 backend/migrations/versions/0011_alert_lifecycle_projection.py
- 修改 backend/src/incident_intelligence/persistence/models.py
- 新建 backend/tests/integration/persistence/test_alert_lifecycle_schema.py

- [ ] 步骤 1：先写数据库约束测试

测试唯一身份、ACTIVE 不能设置 resolved_at、RESOLVED 必须设置 resolved_at、version 大于等于 1，以及迁移升级/降级。

~~~python
def test_alert_identity_is_unique(session, alert_source_row):
    session.add(alert_row(alert_source_row.id, "fp-1", utc("2026-08-31T10:00:00Z")))
    session.commit()
    session.add(alert_row(alert_source_row.id, "fp-1", utc("2026-08-31T10:00:00Z")))
    with pytest.raises(IntegrityError):
        session.commit()
~~~

- [ ] 步骤 2：运行测试，确认表尚不存在

~~~bash
cd backend
.venv/bin/pytest tests/integration/persistence/test_alert_lifecycle_schema.py -q
~~~

- [ ] 步骤 3：创建迁移与 ORM

alert_lifecycles 字段：

- id、alert_source_id、source_alert_key、episode_started_at
- alert_name、summary、description
- state、severity、environment、service
- entity_type、entity_key、entity_display_name
- first_observed_at、last_observed_at
- first_received_at、last_received_at、resolved_at
- firing_observed、version、created_at、updated_at

约束和索引：

~~~python
sa.UniqueConstraint(
    "alert_source_id", "source_alert_key", "episode_started_at",
    name="uq_alert_lifecycle_identity",
)
sa.CheckConstraint("version >= 1", name="ck_alert_lifecycle_version")
sa.CheckConstraint(
    "(state = 'ACTIVE' AND resolved_at IS NULL AND firing_observed = 1) "
    "OR (state = 'RESOLVED' AND resolved_at IS NOT NULL)",
    name="ck_alert_lifecycle_state",
)
~~~

同时：

- signal_events 增加可空 source_alert_key、episode_started_at、alert_name、description；新写入必须有值，历史行允许为空。
- signal_intake_results 增加可空 alert_lifecycle_id 外键；旧 alert_id 保留但停止写入。
- 添加 state、first_received_at、(alert_source_id, first_received_at) 索引。
- downgrade 按外键、索引、字段、表的逆序撤销。

- [ ] 步骤 4：验证升级、降级、再升级

~~~bash
cd backend
.venv/bin/alembic upgrade head
.venv/bin/pytest tests/integration/persistence/test_alert_lifecycle_schema.py -q
.venv/bin/alembic downgrade 0010_alert_event_clustering
.venv/bin/alembic upgrade head
~~~

- [ ] 步骤 5：提交

~~~bash
git add backend/migrations/versions/0011_alert_lifecycle_projection.py backend/src/incident_intelligence/persistence/models.py backend/tests/integration/persistence/test_alert_lifecycle_schema.py
git commit -m "feat: 建立告警生命周期存储"
~~~

## 任务 3：实现纯领域状态机

涉及文件：

- 新建 backend/src/incident_intelligence/domain/alerts.py
- 新建 backend/tests/unit/domain/test_alert_lifecycle.py

- [ ] 步骤 1：先写状态转换测试

覆盖：

| 已有状态 | 新信号 | 结果 | 是否新建 | outcome |
|---|---|---|---|---|
| 无 | firing | ACTIVE | 是 | opened |
| ACTIVE | firing | ACTIVE | 否 | updated |
| ACTIVE | resolved | RESOLVED | 否 | resolved |
| 无 | resolved | RESOLVED | 是 | orphan_resolved |
| RESOLVED | 晚到 firing | RESOLVED | 否 | updated |

另外验证：相同 fingerprint、不同 episode_started_at 是两轮 Alert；晚到 firing 不能重新打开。

- [ ] 步骤 2：运行测试，确认失败

~~~bash
cd backend
.venv/bin/pytest tests/unit/domain/test_alert_lifecycle.py -q
~~~

- [ ] 步骤 3：实现不可变模型与纯函数

~~~python
AlertState = Literal["ACTIVE", "RESOLVED"]
AlertProjectionOutcome = Literal["opened", "updated", "resolved", "orphan_resolved"]

class Alert(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    alert_source_id: str
    source_alert_key: str
    episode_started_at: AwareDatetime
    alert_name: str
    summary: str
    description: str
    state: AlertState
    severity: str
    environment: str
    service: str | None
    entity_type: str
    entity_key: str
    entity_display_name: str
    first_observed_at: AwareDatetime
    last_observed_at: AwareDatetime
    first_received_at: AwareDatetime
    last_received_at: AwareDatetime
    resolved_at: AwareDatetime | None
    firing_observed: bool
    version: int
    created_at: AwareDatetime
    updated_at: AwareDatetime

~~~

实现函数签名为 project_alert(existing: Alert | None, command: SignalCommand, *, received_at: datetime, alert_id: str | None) -> AlertProjection。函数不访问数据库、不读取当前时间、不生成随机 ID。字段更新采用“非空新值覆盖，身份字段永不改变”。Watchdog 判断放在接入编排层。

- [ ] 步骤 4：验证并提交

~~~bash
cd backend
.venv/bin/pytest tests/unit/domain/test_alert_lifecycle.py -q
git add backend/src/incident_intelligence/domain/alerts.py backend/tests/unit/domain/test_alert_lifecycle.py
git commit -m "feat: 实现告警生命周期状态机"
~~~

## 任务 4：将投影接入同一写事务

涉及文件：

- 新建 backend/src/incident_intelligence/persistence/alert_lifecycle_repository.py
- 修改 backend/src/incident_intelligence/persistence/unit_of_work.py
- 修改 backend/src/incident_intelligence/services/signal_intake.py
- 修改 backend/tests/integration/services/test_signal_intake_service.py
- 新建 backend/tests/integration/services/test_alert_lifecycle_projection.py

- [ ] 步骤 1：先写集成失败测试

必须证明：

- firing 与 resolved 返回同一个 alert_id，表内只有一行且为 RESOLVED。
- 同一 fingerprint 的新 episode 创建第二行。
- 精确重放返回原 alert_id，不更新计数。
- 投影保存失败时 SignalEvent 一并回滚。
- 并发首次 firing 最终只产生一行。
- resolved 先到后晚到 firing，仍保持 RESOLVED。

~~~python
def test_firing_and_resolved_share_one_alert(session, intake_service):
    firing = intake_service.submit(batch(firing_command()))
    resolved = intake_service.submit(batch(resolved_command()))
    assert firing.items[0].alert_id == resolved.items[0].alert_id
    assert session.scalar(select(func.count(AlertLifecycleRow.id))) == 1
    assert session.get(AlertLifecycleRow, firing.items[0].alert_id).state == "RESOLVED"
~~~

- [ ] 步骤 2：运行测试，确认失败

~~~bash
cd backend
.venv/bin/pytest tests/integration/services/test_signal_intake_service.py tests/integration/services/test_alert_lifecycle_projection.py -q
~~~

- [ ] 步骤 3：实现仓储

接口固定为三个方法：get_by_identity(alert_source_id, source_alert_key, episode_started_at, for_update=False) -> Alert | None、insert(alert: Alert) -> None、update(alert: Alert, expected_version: int) -> bool。

get_by_identity(for_update=True) 使用 SELECT FOR UPDATE；update 使用乐观版本条件。唯一键冲突交给现有事务重试收敛到同一行。

- [ ] 步骤 4：接入 SignalIntakeService

事务内顺序固定：

1. 检查精确重放。
2. 保存含标准化身份的 SignalEvent。
3. Watchdog 只保存 ignored 接入结果，不建 Alert。
4. 锁定逻辑身份，调用 project_alert。
5. 插入或更新 Alert。
6. 写 signal_intake_results.alert_lifecycle_id。
7. 写 signal.received 与 alert.opened/updated/resolved 有界审计。
8. 更新 receipt 的 opened、updated、resolved 统计。
9. 提交并返回 alert_id。

返回模型：

~~~python
class SignalIntakeItemResult(BaseModel):
    source_event_id: str
    signal_event_id: str
    alert_id: str | None
    outcome: Literal["opened", "updated", "resolved", "orphan_resolved", "ignored"]
    replayed: bool
~~~

- [ ] 步骤 5：验证并提交

~~~bash
cd backend
.venv/bin/pytest tests/integration/services/test_signal_intake_service.py tests/integration/services/test_alert_lifecycle_projection.py -q
.venv/bin/pytest tests/unit tests/integration -q
git add backend/src/incident_intelligence/persistence backend/src/incident_intelligence/services/signal_intake.py backend/tests/integration/services
git commit -m "feat: 在接入事务中投影告警生命周期"
~~~

## 任务 5：可重复回填历史 SignalEvent

涉及文件：

- 新建 backend/src/incident_intelligence/services/alert_lifecycle_backfill.py
- 新建 backend/scripts/backfill_alert_lifecycles.py
- 新建 backend/tests/integration/services/test_alert_lifecycle_backfill.py

- [ ] 步骤 1：先写回填失败测试

覆盖标准身份完整、老数据缺身份、resolved 先到、重复运行、单批失败回滚。

~~~python
def test_backfill_is_repeatable_and_merges_firing_with_resolved(session):
    seed_legacy_firing_and_resolved(session)
    first = backfill_alert_lifecycles(session, batch_size=100)
    second = backfill_alert_lifecycles(session, batch_size=100)
    assert first.created == 1
    assert second.created == 0
    assert session.scalar(select(func.count(AlertLifecycleRow.id))) == 1
    assert session.scalar(select(AlertLifecycleRow.state)) == "RESOLVED"
~~~

- [ ] 步骤 2：运行测试，确认失败

~~~bash
cd backend
.venv/bin/pytest tests/integration/services/test_alert_lifecycle_backfill.py -q
~~~

- [ ] 步骤 3：实现保守回填

规则：

1. 有 source_alert_key 与 episode_started_at 时使用精确身份。
2. 老数据按 alert_source_id + alert_name + entity_key 计算稳定 legacy 哈希键。
3. 老 firing 以 observed_at 作为 episode 开始；resolved 只配对最近一个尚未恢复的 firing。
4. 无法唯一配对时创建 firing_observed=false 的独立 RESOLVED Alert，不猜测。
5. Watchdog 跳过。
6. Alert ID 由唯一身份稳定计算；重复运行不新增。
7. 按 SignalEvent ID 游标分批；每批独立事务，失败批次整体回滚。
8. 命令行只输出数量和游标，不输出 payload、标签或 Secret。

- [ ] 步骤 4：验证并提交

~~~bash
cd backend
.venv/bin/pytest tests/integration/services/test_alert_lifecycle_backfill.py -q
.venv/bin/python scripts/backfill_alert_lifecycles.py --dry-run --batch-size 500
git add backend/src/incident_intelligence/services/alert_lifecycle_backfill.py backend/scripts/backfill_alert_lifecycles.py backend/tests/integration/services/test_alert_lifecycle_backfill.py
git commit -m "feat: 增加历史告警生命周期回填"
~~~

## 任务 6：把告警 API 切换到 Alert

涉及文件：

- 修改 backend/src/incident_intelligence/persistence/alert_center_repository.py
- 修改 backend/src/incident_intelligence/services/alert_center.py
- 修改 backend/src/incident_intelligence/api/schemas/alerts.py
- 修改 backend/src/incident_intelligence/api/routes/alerts.py
- 修改 backend/tests/api/test_alerts.py

- [ ] 步骤 1：先改 API 失败测试

验证 firing + resolved 后 total 为 1、两次响应同 alert_id、状态 RESOLVED、趋势总数为 1。详情 alert_name 必须精确等于 labels.alertname，响应中不包含 facts 或 SignalEvent 时间线。

- [ ] 步骤 2：运行测试，确认旧读模型失败

~~~bash
cd backend
.venv/bin/pytest tests/api/test_alerts.py -q
~~~

- [ ] 步骤 3：替换读仓储与响应

保留 URL：

- GET /api/v1/alerts
- GET /api/v1/alerts/{alert_id}
- GET /api/v1/alerts/timeseries

列表筛选支持 state、severity、source_id、environment、service、query 和时间范围。接收时间过滤 first_received_at，趋势同样按 first_received_at 分桶，basis 固定返回 first_received_at。

列表返回 id、alert_name、summary、state、severity、来源、环境、服务、资源、episode_started_at、首次/最近接收、resolved_at 和 duration_seconds。详情增加 description、首次/最近观测时间、firing_observed。

- [ ] 步骤 4：验证并提交

~~~bash
cd backend
.venv/bin/pytest tests/api/test_alerts.py -q
.venv/bin/pytest tests/api -q
git add backend/src/incident_intelligence/persistence/alert_center_repository.py backend/src/incident_intelligence/services/alert_center.py backend/src/incident_intelligence/api/schemas/alerts.py backend/src/incident_intelligence/api/routes/alerts.py backend/tests/api/test_alerts.py
git commit -m "feat: 告警接口切换到生命周期投影"
~~~

## 任务 7：前端展示单行 Alert 生命周期

涉及文件：

- 修改 frontend/src/App.vue
- 修改 frontend/src/api/alerts.js
- 修改 frontend/src/composables/useAlertCenter.js
- 修改 frontend/src/components/AlertCenter.vue
- 修改 frontend/src/components/AlertTrendChart.vue
- 修改 frontend/src/api/alerts.test.js
- 修改 frontend/src/components/AlertCenter.test.js
- 修改 frontend/src/App.test.js

- [ ] 步骤 1：先更新组件失败测试

固定行为：

- 标题为“告警中心”，不再叫“原始告警”。
- 一轮 firing/resolved 只渲染一行，状态“已恢复”。
- 主标题使用 alert_name，summary 单独显示。
- 点击行只展开生命周期详情，不显示 facts、标签墙或 SignalEvent 时间线。
- 状态筛选为“全部 / 告警中 / 已恢复”。
- 图表表示“新告警数量”，拖选仍联动列表。

- [ ] 步骤 2：运行测试，确认旧字段失败

~~~bash
cd frontend
npm test -- --run src/api/alerts.test.js src/components/AlertCenter.test.js src/App.test.js
~~~

- [ ] 步骤 3：实现页面切换

- API 方法改为 fetchAlerts、fetchAlert、fetchAlertTrend。
- eventType 筛选改为 state，发送 ACTIVE 或 RESOLVED。
- 行内展示状态、alert_name、summary、严重度、服务或资源、接入源、首次触发。
- 展开区集中展示描述、环境、服务、资源、首次触发、最近更新、恢复时间、持续时长、接入源。
- 删除 facts 开关、标签块和对应本地状态。
- 保留快捷时间、自定义时间和图表拖选；文案改为“按平台首次接收时间统计新告警数量”。

- [ ] 步骤 4：验证并提交

~~~bash
cd frontend
npm test -- --run
npm run build
git add src
git commit -m "feat: 前端展示告警生命周期"
~~~

## 任务 8：回填、文档和端到端验收

涉及文件：

- 修改 docs/product.md
- 修改 docs/architecture.md
- 修改 docs/current-state.md
- 修改 specs/active/multi-source-incident-center.md
- 必要时修改 docs/superpowers/specs/2026-08-31-alert-lifecycle-projection-design.md

- [ ] 步骤 1：更新项目事实

文档明确 SignalEvent/Alert 边界、唯一身份、状态转换、各适配器名称规则、Watchdog 排除、趋势口径、保守历史回填。Incident、自动取证和 AI 继续标为计划能力。

- [ ] 步骤 2：升级并正式回填

确认终端已设置 II_DATABASE_URL 后：

~~~bash
cd backend
.venv/bin/alembic upgrade head
.venv/bin/python scripts/backfill_alert_lifecycles.py --batch-size 500
.venv/bin/python scripts/backfill_alert_lifecycles.py --batch-size 500
~~~

第二次 created 必须为 0。

- [ ] 步骤 3：运行统一验证

~~~bash
cd backend
./scripts/verify-backend.sh
cd ../frontend
npm test -- --run
npm run build
cd ..
git diff --check
~~~

若后端脚本要求测试库，使用终端中已有 II_TEST_DATABASE_URL，不把密码写入仓库或计划。

- [ ] 步骤 4：真实行为验收

用现有 Alertmanager 接入源发送同一轮 firing、resolved，确认：

1. 两次接入成功并返回同一 alert_id。
2. 列表一行且状态“已恢复”。
3. alert_name 精确等于 labels.alertname。
4. 趋势只增加 1。
5. 同 fingerprint、不同 startsAt 再 firing 后列表增加到 2。
6. Watchdog 可接收审计，但不出现在列表和趋势。

- [ ] 步骤 5：提交文档并检查

~~~bash
git add docs specs/active/multi-source-incident-center.md
git commit -m "docs: 记录告警生命周期实现状态"
git status --short
git log -8 --oneline
~~~

最终汇报包含迁移和回填数量、后端验证、前端测试与构建、真实 firing/resolved 验收，以及本轮未实现能力。

## 完成定义

- 缺少 labels.alertname 的 Alertmanager 输入明确失败。
- 同一逻辑身份永远只有一条 Alert。
- firing → resolved 不新增列表行和趋势数量。
- resolved 先到可形成可解释的 RESOLVED Alert，晚到 firing 不重开。
- 新 episode 创建新 Alert。
- 写入同事务成功或回滚。
- 幂等、批次、并发和历史回填均有自动测试。
- 前端不再把 SignalEvent 当业务告警。
- Watchdog 不进入 Alert。
- 后端统一验证、前端测试、构建和 git diff --check 全部通过。
