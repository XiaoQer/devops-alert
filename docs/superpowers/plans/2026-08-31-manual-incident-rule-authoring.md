# 手动 Incident 规则配置实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本仓库禁止子 Agent，所有任务在当前会话顺序完成。

**目标：** 交付无内置规则的 Incident 规则管理闭环，让用户能通过四步向导创建规则、用真实历史 Alert 试运行并安全发布或停用。

**架构：** 后端新增独立的规则领域模型、只读历史窗口评估器、MySQL 仓储和管理 API；规则只读取 `alert_lifecycles`，不创建 Incident。前端增加独立规则中心与四步向导，所有展示和试运行结果来自真实 API。

**技术栈：** Python 3.13+、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、MySQL 8.4、Vue 3、Vitest、Phosphor Icons。

**规格：** `docs/superpowers/specs/2026-08-31-manual-incident-rule-authoring-design.md`

## 全局约束

- 所有变更直接在 `main` 分支完成，不创建分支、worktree 或子 Agent。
- 不提供内置规则、模板、原始 JSON、正则、脚本或 OR 条件。
- 规则只读取 Alert，不读取 SignalEvent、故障注入平台、Kubernetes 或外部系统。
- 试运行不得创建或修改 Incident、Alert、SignalEvent。
- 发布规则本期不自动生成 Incident；页面必须明确下一阶段边界。
- 环境必选；观察窗口 1–60 分钟；历史范围只允许 1/6/12/24/48 小时。
- 最多扫描 10,000 条 Alert、返回 100 个命中、每个命中最多 10 条示例；截断结果不能发布。
- 所有写操作使用乐观版本；审计不保存 Secret 或完整原始请求。
- 页面使用用户选择的第 1 个分步向导视觉，延续现有深色石墨设计和 14–16px 正文。

---

### 任务 1：建立规则领域模型和状态机

**文件：**
- 新建：`backend/src/incident_intelligence/domain/incident_rules.py`
- 修改：`backend/src/incident_intelligence/ids.py`
- 测试：`backend/tests/unit/domain/test_incident_rules.py`

**接口：**
- 产出：`IncidentRuleConfig`、`IncidentRule`、`IncidentRuleCondition`、`IncidentRuleState`、`RuleGroupBy`。
- 产出：`create_rule()`、`update_draft()`、`record_successful_dry_run()`、`publish_rule()`、`disable_rule()`、`copy_rule()`、`summarize_rule()`。
- ID 前缀：规则 `irl`、试运行 `ird`、操作审计 `iro`。

- [ ] **步骤 1：编写领域失败测试**

```python
def test_rule_requires_environment_count_condition_and_unique_condition_types():
    with pytest.raises(ValidationError):
        IncidentRuleConfig(
            environment="",
            alert_source_ids=(),
            services=(),
            group_by="SERVICE",
            window_minutes=5,
            conditions=(
                {"type": "MAX_SEVERITY_AT_LEAST", "severity": "high"},
            ),
        )


def test_modifying_draft_invalidates_previous_dry_run():
    tested = record_successful_dry_run(make_draft(), dry_run_id="ird_" + "1" * 32)
    changed = update_draft(tested, name="支付链路异常")
    assert changed.version == tested.version + 1
    assert changed.last_successful_dry_run_version is None


def test_published_rule_is_immutable_and_can_be_disabled():
    published = publish_rule(make_tested_draft())
    with pytest.raises(RuleStateConflict):
        update_draft(published, name="不能覆盖")
    assert disable_rule(published).state == "DISABLED"
```

- [ ] **步骤 2：运行测试并确认因模块不存在而失败**

运行：`cd backend && .venv/bin/python -m pytest tests/unit/domain/test_incident_rules.py -q`

预期：测试收集失败，提示 `incident_rules` 模块不存在。

- [ ] **步骤 3：实现最小领域模型**

使用 Pydantic 判别联合表达四种条件；构造时校验条件唯一、计数阈值 1–1000、至少一个计数条件、窗口 1–60、来源和服务数量有界。状态转换返回新的冻结模型，不原地修改；中文摘要固定由后端生成。

- [ ] **步骤 4：运行领域测试并确认通过**

运行：`cd backend && .venv/bin/python -m pytest tests/unit/domain/test_incident_rules.py -q`

- [ ] **步骤 5：提交**

提交信息：`feat: 建立 Incident 规则领域模型`

### 任务 2：实现真实历史 Alert 试运行评估器

**文件：**
- 新建：`backend/src/incident_intelligence/domain/incident_rule_evaluation.py`
- 测试：`backend/tests/unit/domain/test_incident_rule_evaluation.py`

**接口：**
- 消费：`IncidentRuleConfig` 与按 `first_received_at` 升序排列的 `AlertEvaluationFact`。
- 产出：`evaluate_rule(config, alerts, *, max_matches=100) -> EvaluationResult`。
- 结果：扫描数、命中窗口、分组、计数统计、最高级别、示例 Alert ID 和 `truncated`。

- [ ] **步骤 1：编写滚动窗口失败测试**

```python
def test_evaluator_groups_by_service_and_applies_all_conditions():
    result = evaluate_rule(
        config=service_rule(
            distinct_names=2,
            severity_at_least="high",
            window_minutes=5,
        ),
        alerts=(
            fact("alt_a", "checkout", "HighLatency", "medium", minute=0),
            fact("alt_b", "checkout", "ErrorRate", "high", minute=3),
            fact("alt_c", "catalog", "ErrorRate", "critical", minute=3),
        ),
    )
    assert len(result.matches) == 1
    assert result.matches[0].group_display_name == "checkout"
    assert result.matches[0].distinct_alert_names == 2
    assert result.matches[0].example_alert_ids == ("alt_a", "alt_b")


def test_service_rule_excludes_alert_without_service():
    result = evaluate_rule(service_rule(distinct_names=1), (fact("alt_a", None),))
    assert result.matches == ()


def test_evaluator_merges_consecutive_windows_with_same_members():
    result = evaluate_rule(service_rule(distinct_names=2), duplicate_window_facts())
    assert len(result.matches) == 1
```

- [ ] **步骤 2：运行测试并确认评估器缺失导致失败**

运行：`cd backend && .venv/bin/python -m pytest tests/unit/domain/test_incident_rule_evaluation.py -q`

- [ ] **步骤 3：实现纯函数评估器**

先按环境与过滤条件筛选，再按 service 或 entity_key 分组。对每个分组维护滚动队列；以每条 Alert 首次接收时间为窗口终点，计算活动数量、不同名称、不同实体和最高级别；AND 条件全满足才输出。连续结果的成员 ID 完全一致时只保留一个。

- [ ] **步骤 4：增加边界失败测试并实现截断**

```python
def test_evaluator_marks_result_truncated_at_match_limit():
    result = evaluate_rule(entity_rule(active_alerts=1), many_independent_facts(), max_matches=2)
    assert len(result.matches) == 2
    assert result.truncated is True
```

- [ ] **步骤 5：运行评估器测试并提交**

运行：`cd backend && .venv/bin/python -m pytest tests/unit/domain/test_incident_rule_evaluation.py -q`

提交信息：`feat: 实现 Incident 规则历史评估器`

### 任务 3：增加 MySQL 表、仓储和有界 Alert 查询

**文件：**
- 新建：`backend/migrations/versions/0012_incident_rule_authoring.py`
- 修改：`backend/src/incident_intelligence/persistence/models.py`
- 新建：`backend/src/incident_intelligence/persistence/incident_rule_repository.py`
- 修改：`backend/src/incident_intelligence/persistence/alert_center_repository.py`
- 修改：`backend/src/incident_intelligence/persistence/unit_of_work.py`
- 测试：`backend/tests/integration/persistence/test_incident_rule_schema.py`
- 测试：`backend/tests/integration/services/test_incident_rule_repository.py`

**接口：**
- 产出：`IncidentRuleRepository` 的 `insert/get/list/update/delete_draft/insert_dry_run/insert_operation`。
- 产出：`AlertRepository.list_for_rule_evaluation(..., limit=10_001)`，返回稳定升序事实。

- [ ] **步骤 1：编写迁移失败测试**

```python
def test_incident_rule_schema_has_state_version_and_audit_constraints(migrated_engine):
    inspector = inspect(migrated_engine)
    assert {"incident_rules", "incident_rule_dry_runs", "incident_rule_operations"} <= set(
        inspector.get_table_names()
    )
    assert "rule_name" in {item["name"] for item in inspector.get_unique_constraints("incident_rules")}
```

- [ ] **步骤 2：运行迁移测试并确认表不存在**

运行：`cd backend && .venv/bin/python -m pytest tests/integration/persistence/test_incident_rule_schema.py -q`

- [ ] **步骤 3：实现迁移与 ORM 行模型**

`incident_rules` 使用 JSON 保存来源、服务和结构化条件，同时对状态、窗口、版本建立数据库约束；`dry_runs` 保存有界结果 JSON；`operations` 保存动作、操作者、规则版本、时间和安全摘要。三表均使用 InnoDB、utf8mb4_bin 和微秒 UTC 时间。

- [ ] **步骤 4：编写仓储失败测试**

```python
def test_repository_update_uses_expected_version(session):
    repository = IncidentRuleRepository(session)
    repository.insert(make_rule(version=1))
    assert repository.update(make_rule(version=2), expected_version=1) is True
    assert repository.update(make_rule(version=3), expected_version=1) is False


def test_evaluation_query_is_bounded_and_ordered(session):
    insert_alerts(session, received_minutes=(3, 1, 2))
    rows = AlertRepository(session).list_for_rule_evaluation(
        environment="staging", alert_source_ids=(), services=(),
        received_from=START, received_to=END, limit=2,
    )
    assert [row.id for row in rows] == ["alt_1", "alt_2"]
```

- [ ] **步骤 5：实现仓储、工作单元接线并运行测试**

运行：`cd backend && .venv/bin/python -m pytest tests/integration/persistence/test_incident_rule_schema.py tests/integration/services/test_incident_rule_repository.py -q`

- [ ] **步骤 6：提交**

提交信息：`feat: 持久化 Incident 规则和试运行`

### 任务 4：实现规则应用服务与管理 API

**文件：**
- 新建：`backend/src/incident_intelligence/services/incident_rules.py`
- 新建：`backend/src/incident_intelligence/api/schemas/incident_rules.py`
- 新建：`backend/src/incident_intelligence/api/routes/incident_rules.py`
- 修改：`backend/src/incident_intelligence/api/dependencies.py`
- 修改：`backend/src/incident_intelligence/api/router.py`
- 修改：`backend/src/incident_intelligence/main.py`
- 测试：`backend/tests/integration/services/test_incident_rule_service.py`
- 测试：`backend/tests/api/test_incident_rules.py`

**接口：**
- 产出：`IncidentRuleService.create/list/get/update/delete_draft/dry_run/publish/disable/copy`。
- 所有写 API 需要现有人工 API Bearer Token；创建、修改、删除、发布、停用和复制要求 `Idempotency-Key`。

- [ ] **步骤 1：编写服务失败测试**

```python
def test_publish_requires_current_successful_untruncated_dry_run(service):
    rule = service.create(valid_create_command(), actor="tester", idempotency_key="create")
    with pytest.raises(RulePublishBlocked):
        service.publish(rule.id, expected_version=rule.version, actor="tester", idempotency_key="publish")


def test_dry_run_reads_real_alerts_without_creating_incident(service, alert_fixture):
    result = service.dry_run(alert_fixture.rule_id, history_hours=6, actor="tester")
    assert result.scanned_alert_count == 3
    assert result.match_count == 1
    assert count_rows("incidents") == 0
```

- [ ] **步骤 2：运行服务测试并确认服务不存在**

运行：`cd backend && .venv/bin/python -m pytest tests/integration/services/test_incident_rule_service.py -q`

- [ ] **步骤 3：实现应用服务和错误类型**

服务在单事务内写规则和操作审计；试运行查询 10,001 条判断扫描截断，将最多 10,000 条交给评估器；只有当前版本、未截断、成功试运行过的草稿可发布。

- [ ] **步骤 4：编写 API 契约失败测试**

```python
def test_rule_api_supports_create_dry_run_and_publish(api_client, auth_headers):
    created = api_client.post(
        "/api/v1/incident-rules", json=valid_payload(),
        headers={**auth_headers, "Idempotency-Key": "create-1"},
    )
    assert created.status_code == 201
    rule = created.json()
    dry_run = api_client.post(
        f"/api/v1/incident-rules/{rule['id']}/dry-runs",
        json={"history_hours": 6, "expected_version": rule["version"]},
        headers=auth_headers,
    )
    assert dry_run.status_code == 200
```

- [ ] **步骤 5：实现 Pydantic Schema、路由和依赖接线**

状态冲突返回 409，找不到返回 404，字段校验返回 422，发布前置条件返回 409；消息均为中文安全提示。

- [ ] **步骤 6：运行后端相关测试并提交**

运行：`cd backend && .venv/bin/python -m pytest tests/unit/domain/test_incident_rules.py tests/unit/domain/test_incident_rule_evaluation.py tests/integration/persistence/test_incident_rule_schema.py tests/integration/services/test_incident_rule_repository.py tests/integration/services/test_incident_rule_service.py tests/api/test_incident_rules.py -q`

提交信息：`feat: 提供 Incident 规则管理 API`

### 任务 5：建立前端 API 与向导状态管理

**文件：**
- 新建：`frontend/src/api/incidentRules.js`
- 新建：`frontend/src/api/incidentRules.test.js`
- 新建：`frontend/src/composables/useIncidentRules.js`
- 新建：`frontend/src/composables/useIncidentRules.test.js`
- 新建：`frontend/src/presentation/incidentRuleView.js`
- 新建：`frontend/src/presentation/incidentRuleView.test.js`

**接口：**
- 产出：规则列表、详情、创建、修改、删除、试运行、发布、停用和复制的 API 函数。
- 产出：`useIncidentRules()`，管理列表、向导步骤、草稿、字段错误、保存、试运行和发布状态。

- [ ] **步骤 1：编写 API 请求失败测试**

```javascript
it("把真实试运行范围和当前版本发送给后端", async () => {
  fetch.mockResolvedValue(jsonResponse({ matches: [] }));
  await dryRunIncidentRule("irl_1", { history_hours: 6, expected_version: 3 });
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/incident-rules/irl_1/dry-runs",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ history_hours: 6, expected_version: 3 }) }),
  );
});
```

- [ ] **步骤 2：运行并确认模块不存在导致失败**

运行：`cd frontend && npm test -- src/api/incidentRules.test.js src/composables/useIncidentRules.test.js src/presentation/incidentRuleView.test.js`

- [ ] **步骤 3：实现 API、展示转换和状态管理**

草稿只在后端成功后标记已保存；操作失败保留输入；规则版本冲突进入 `conflict` 状态；任何字段变化都清空前端旧试运行结果；发布按钮只依赖后端返回的 `publishable`。

- [ ] **步骤 4：运行前端状态测试并提交**

运行：`cd frontend && npm test -- src/api/incidentRules.test.js src/composables/useIncidentRules.test.js src/presentation/incidentRuleView.test.js`

提交信息：`feat: 接入 Incident 规则前端状态`

### 任务 6：实现规则列表和四步创建界面

**文件：**
- 新建：`frontend/src/components/IncidentRuleCenter.vue`
- 新建：`frontend/src/components/IncidentRuleCenter.test.js`
- 新建：`frontend/src/components/IncidentRuleWizard.vue`
- 新建：`frontend/src/components/IncidentRuleWizard.test.js`
- 修改：`frontend/src/App.vue`
- 修改：`frontend/src/App.test.js`
- 修改：`frontend/src/styles.css`
- 修改：`frontend/AGENTS.md`

**视觉目标：** `/Users/shaoqian.li/.codex/generated_images/01a017a7-cb3c-70d2-a5a1-5b3c1030ff47/exec-00dd3b7d-a33e-407e-a5b4-18a2accbb880.png`

- [ ] **步骤 1：编写空状态与导航失败测试**

```javascript
it("没有规则时只显示真实空状态", async () => {
  fetchIncidentRules.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  const wrapper = mount(IncidentRuleCenter);
  await flushPromises();
  expect(wrapper.text()).toContain("还没有 Incident 规则");
  expect(wrapper.text()).not.toContain("示例规则");
});
```

- [ ] **步骤 2：运行组件测试并确认组件不存在**

运行：`cd frontend && npm test -- src/components/IncidentRuleCenter.test.js src/components/IncidentRuleWizard.test.js src/App.test.js`

- [ ] **步骤 3：实现列表和四步向导**

严格复现选定方案：左侧步骤、中央表单、右侧中文摘要、底部固定动作；步骤 4 展示真实扫描数、命中数、是否截断和命中分组。使用 Phosphor 图标，不绘制内联 SVG，不增加演示数据。

- [ ] **步骤 4：补充主流程失败测试并实现交互**

```javascript
it("完成真实试运行后才能发布", async () => {
  const wrapper = mount(IncidentRuleWizard, { props: { rule: savedDraft() } });
  await goToStep(wrapper, 4);
  expect(wrapper.get("[data-testid='publish-rule']").attributes("disabled")).toBeDefined();
  await wrapper.get("[data-testid='run-rule']").trigger("click");
  await flushPromises();
  expect(wrapper.text()).toContain("扫描 48 条告警，命中 2 个窗口");
  expect(wrapper.get("[data-testid='publish-rule']").attributes("disabled")).toBeUndefined();
});
```

- [ ] **步骤 5：实现响应式布局并更新前端设计约定**

1440px 使用三栏向导；低于 1100px 时摘要移到表单下方；低于 760px 时步骤变为顶部横向并允许内容纵向滚动，页面不得横向溢出。

- [ ] **步骤 6：运行前端测试与构建并提交**

运行：`cd frontend && npm test && npm run build && npm run test:sites`

提交信息：`feat: 实现 Incident 规则分步向导`

### 任务 7：迁移本地数据库、全量验证和设计 QA

**文件：**
- 修改：`docs/product.md`
- 修改：`docs/architecture.md`
- 修改：`docs/current-state.md`
- 修改：`specs/active/manual-incident-rule-authoring.md`
- 新建：`docs/verification/2026-08-31-manual-incident-rule-authoring.md`
- 修改：`frontend/design-qa.md`

- [ ] **步骤 1：升级本地 MySQL 并验证空规则列表**

运行：`cd backend && .venv/bin/python -m alembic upgrade head`

验证：`GET /api/v1/incident-rules` 返回 `items=[]` 或真实已有规则，不出现系统生成规则。

- [ ] **步骤 2：运行统一后端验证**

运行：`./scripts/verify-backend.sh`

预期：ruff、格式、mypy、pytest 全部通过，覆盖率不低于 90%。

- [ ] **步骤 3：运行完整前端验证**

运行：`cd frontend && npm test && npm run build && npm run test:sites`

- [ ] **步骤 4：启动前后端并执行真实浏览器主流程**

后端运行在 `127.0.0.1:8000`，前端运行在 `127.0.0.1:5173`。在应用内浏览器完成：进入规则页、创建草稿、四步填写、真实试运行、发布、停用、复制。

- [ ] **步骤 5：完成阻断式设计 QA**

使用同一 1440×1024 视口分别打开选定视觉目标和实现截图，合并比较布局、密度、字体、间距、边框、颜色和交互状态。把报告写入 `frontend/design-qa.md`；修复所有 P0/P1/P2，直到写明 `final result: passed`。

- [ ] **步骤 6：更新产品事实和验证记录**

文档明确：规则管理和历史试运行已实现；发布规则尚不生成 Incident；下一步是候选 Incident 引擎设计。将活跃规格状态改为已验证并移入 `specs/completed/`。

- [ ] **步骤 7：提交最终验证结果**

提交信息：`docs: 完成 Incident 规则配置验收`
