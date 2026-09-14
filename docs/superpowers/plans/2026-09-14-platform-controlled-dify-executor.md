# 平台主控 Dify 执行器实施计划

> **执行约束：** 本项目由当前会话直接在 `main` 分支顺序实施；不创建功能分支、工作树或子 Agent。每项先写失败测试，再实现最小改动。

**目标：** 由平台主程序控制 Dify Workflow 的诊断任务、权限、输入、工具、审计和报告发布。

**架构：** 平台是 Incident、Alert、Evidence、知识、权限和诊断状态的唯一事实源。Dify 是无状态执行器，只拿到运行号和短期能力凭证，并通过平台三项受限只读工具按需取数。

**技术栈：** Python、FastAPI、Pydantic、SQLAlchemy、Alembic、MySQL、HTTPX、Qdrant、Vue。

**设计依据：** `docs/superpowers/specs/2026-09-14-platform-controlled-dify-executor-design.md`

## 全局约束

- Dify 不得直接访问 MySQL、监控系统、飞书、Kubernetes、故障注入平台或任何 Secret。
- 平台是唯一控制面；Dify 不管理任务状态、Incident 状态、通知或报告发布。
- Dify 只可调用 `get_diagnosis_snapshot`、`get_evidence_detail`、`search_incident_knowledge`。
- `scenario_id`、`scenario_version`、`experiment_id`、注入动作和标准答案不得进入诊断、知识或报告。
- 能力凭证不进入数据库正文、日志、API 响应、测试数据或 Dify 输出。
- Dify 总超时 90 秒、响应最大 64 KB；仅超时、限流、临时不可用最多重试三次。

---

### Task 1：固化诊断安全快照与状态机

**Files:**

- Modify: `backend/src/incident_intelligence/domain/diagnosis.py`
- Modify: `backend/src/incident_intelligence/persistence/diagnosis_repository.py`
- Modify: `backend/tests/unit/domain/test_diagnosis.py`
- Modify: `backend/tests/integration/services/test_incident_diagnosis.py`

**Produces:** `transition_diagnosis_run(run, state, now) -> DiagnosisRun`；仅允许 `QUEUED → RUNNING → REPORT_READY|REVIEW_REQUIRED|FAILED`。

- [ ] 写失败测试：

```python
def test_running_run_cannot_return_to_queued() -> None:
    with pytest.raises(ValueError, match="diagnosis_transition_invalid"):
        transition_diagnosis_run(diagnosis_run("RUNNING"), state="QUEUED", now=NOW)

def test_snapshot_rejects_experiment_identity() -> None:
    with pytest.raises(ValueError, match="forbidden_fault_injection_identity"):
        DiagnosisSnapshot.model_validate({**snapshot_payload(), "incident_facts": {"scenario_id": "x"}})
```

- [ ] 运行 `.venv/bin/python -m pytest tests/unit/domain/test_diagnosis.py -q`，确认失败。
- [ ] 实现状态迁移、最多 500 条 Alert 安全事实、100 条 EvidenceItem 指纹和递归实验身份键拒绝。
- [ ] 运行 `II_TEST_DATABASE_URL='mysql+pymysql://root:root@127.0.0.1:3307/mysql' .venv/bin/python -m pytest tests/unit/domain/test_diagnosis.py tests/integration/services/test_incident_diagnosis.py -q`，确认通过。
- [ ] 提交：`git commit -m "feat: 固化诊断状态与安全快照"`。

### Task 2：能力凭证与工具审计

**Files:**

- Create: `backend/src/incident_intelligence/services/diagnosis_capabilities.py`
- Modify: `backend/src/incident_intelligence/persistence/diagnosis_repository.py`
- Modify: `backend/src/incident_intelligence/persistence/models.py`
- Modify: `backend/migrations/versions/0020_controlled_dify_diagnosis.py`
- Create: `backend/tests/unit/services/test_diagnosis_capabilities.py`
- Create: `backend/tests/integration/services/test_diagnosis_tool_receipts.py`

**Produces:** `DiagnosisCapabilityIssuer.issue(run_id, expires_at) -> str` 与 `verify(token, expected_run_id) -> DiagnosisCapability`。

- [ ] 写失败测试：

```python
def test_capability_cannot_read_another_run() -> None:
    token = issuer.issue(RUN_ID, NOW + timedelta(minutes=5))
    with pytest.raises(DiagnosisCapabilityDenied):
        verifier.verify(token, expected_run_id=OTHER_RUN_ID)
```

- [ ] 运行定向测试，确认模块不存在而失败。
- [ ] 使用环境变量 HMAC 密钥签发 `run_id/audience/expires_at/nonce`；验证过期、签名、受众和运行号。回执仅保存请求哈希、结果哈希、截断标记、稳定错误码和时间。
- [ ] 用 MySQL 迁移升降级测试验证回执唯一性与“无 token/secret 列”。
- [ ] 提交：`git commit -m "feat: 增加诊断能力凭证与工具审计"`。

### Task 3：平台受限证据工具

**Files:**

- Create: `backend/src/incident_intelligence/services/diagnosis_tools.py`
- Create: `backend/src/incident_intelligence/api/routes/diagnosis_tools.py`
- Create: `backend/src/incident_intelligence/api/schemas/diagnosis_tools.py`
- Modify: `backend/src/incident_intelligence/api/router.py`
- Modify: `backend/src/incident_intelligence/api/dependencies.py`
- Create: `backend/tests/unit/services/test_diagnosis_tools.py`
- Create: `backend/tests/api/test_diagnosis_tools.py`

**Produces:** `get_diagnosis_snapshot`（最多 40 KB）与 `get_evidence_detail`（单项最多 8 KB）。

- [ ] 写失败测试：

```python
def test_evidence_tool_rejects_item_outside_snapshot(client) -> None:
    response = client.get(url_for(OTHER_EVIDENCE_ID), headers=dify_headers(token_for(RUN_ID)))
    assert response.status_code == 403
    assert response.json()["code"] == "diagnosis_evidence_scope_denied"
```

- [ ] 运行测试确认失败。
- [ ] 工具从能力凭证绑定的快照派生范围；忽略所有外部环境、服务、运行号扩大尝试；允许/拒绝都写回执。
- [ ] 运行 API 与服务测试、Ruff、Mypy；提交：`git commit -m "feat: 提供受控诊断证据工具"`。

### Task 4：受审批知识库与第三项工具

**Files:**

- Modify: `compose.yaml`
- Modify: `backend/pyproject.toml`
- Create: `backend/migrations/versions/0021_incident_knowledge_base.py`
- Create: `backend/src/incident_intelligence/domain/knowledge.py`
- Create: `backend/src/incident_intelligence/persistence/knowledge_repository.py`
- Create: `backend/src/incident_intelligence/adapters/qdrant.py`
- Create: `backend/src/incident_intelligence/services/knowledge_base.py`
- Modify: `backend/src/incident_intelligence/services/diagnosis_tools.py`
- Create: `backend/tests/unit/services/test_knowledge_base.py`
- Create: `backend/tests/unit/adapters/test_qdrant.py`
- Create: `backend/tests/api/test_diagnosis_knowledge_tool.py`

**Produces:** `KnowledgeBaseService.search(scope, query, limit=5)`；scope 只能由 DiagnosisSnapshot 推导。

- [ ] 写失败测试：

```python
def test_search_excludes_draft_and_wrong_environment_chunks() -> None:
    assert service.search(staging_scope(), "MySQL 行锁等待") == (APPROVED_STAGING_CHUNK,)

def test_tool_ignores_caller_environment_override(client) -> None:
    response = client.post(url, json={"query": "行锁等待", "environment": "production"})
    assert all(item["environment"] == "testing" for item in response.json()["items"])
```

- [ ] 运行测试确认失败。
- [ ] MySQL 保存文档、版本、片段和 `DRAFT/APPROVED/RETIRED` 审批状态；Qdrant 仅保存片段 ID、版本、内容哈希、状态和范围元数据。先过滤审批与 scope，后检索；最多 5 段/20 KB。
- [ ] 运行 MySQL 迁移、知识服务、Qdrant 适配器与 API 测试；提交：`git commit -m "feat: 增加受审批 Incident 知识库"`。

### Task 5：平台 Worker 调用固定 Dify Workflow

**Files:**

- Create: `backend/src/incident_intelligence/adapters/dify.py`
- Create: `backend/src/incident_intelligence/services/diagnosis_execution.py`
- Create: `backend/src/incident_intelligence/services/diagnosis_runner.py`
- Modify: `backend/src/incident_intelligence/settings.py`
- Modify: `backend/src/incident_intelligence/main.py`
- Modify: `backend/src/incident_intelligence/persistence/diagnosis_repository.py`
- Create: `backend/tests/unit/adapters/test_dify.py`
- Create: `backend/tests/unit/services/test_diagnosis_execution.py`
- Create: `backend/tests/integration/services/test_diagnosis_execution.py`

**Produces:** `DifyDiagnosisClient.run_workflow(run_id, capability_token)`、`DiagnosisExecutionService.process(task_id)`、`DiagnosisRunner.run_once()`。

- [ ] 写失败测试：

```python
def test_invalid_reference_requires_human_review() -> None:
    client.reply(candidate_with_reference("evitem_not_in_snapshot"))
    assert service.process(TASK_ID).outcome == "REVIEW_REQUIRED"

def test_timeout_reschedules_without_changing_incident() -> None:
    client.raise_timeout()
    assert service.process(TASK_ID).outcome == "RETRY_SCHEDULED"
    assert incident_repository.get(INCIDENT_ID).state == "OPEN"
```

- [ ] 运行测试确认失败。
- [ ] Worker 领取持久化租约、迁移至 RUNNING、签发凭证、单次调用固定 Workflow。适配器限制 TLS、固定地址、90 秒、64 KB；超时/限流/临时不可用退避 5 秒、30 秒、2 分钟，其他错误直接 FAILED。
- [ ] 用 `validate_candidate_report` 校验草案：成功为 REPORT_READY，无效为 REVIEW_REQUIRED；两者都不改变 Incident。
- [ ] 运行适配器、执行与集成测试；提交：`git commit -m "feat: 由平台 Worker 执行受控 Dify 诊断"`。

### Task 6：用户 API、页面和端到端验证

**Files:**

- Create: `backend/src/incident_intelligence/api/routes/incident_diagnosis.py`
- Create: `backend/src/incident_intelligence/api/schemas/incident_diagnosis.py`
- Modify: `backend/src/incident_intelligence/api/router.py`
- Modify: `backend/src/incident_intelligence/api/routes/health.py`
- Create: `frontend/src/api/incidentDiagnosis.js`
- Create: `frontend/src/composables/useIncidentDiagnosis.js`
- Create: `frontend/src/presentation/diagnosisView.js`
- Create: `frontend/src/components/IncidentDiagnosis.vue`
- Modify: `frontend/src/components/IncidentDetail.vue`
- Create: `backend/tests/api/test_incident_diagnosis.py`
- Create: `frontend/src/api/incidentDiagnosis.test.js`
- Create: `frontend/src/presentation/diagnosisView.test.js`
- Create: `frontend/src/components/IncidentDiagnosis.test.js`
- Modify: `docs/current-state.md`
- Modify: `docs/architecture.md`
- Create: `docs/verification/2026-09-14-platform-controlled-dify.md`

- [ ] 写失败测试：

```python
def test_create_diagnosis_requires_user_token_and_idempotency_key(client) -> None:
    assert client.post(create_url, json={"evidence_run_id": RUN_ID}).status_code == 401
```

```javascript
it('不暴露能力凭证或模型思维链', () => {
  expect(diagnosisRunView(internalDetail)).not.toHaveProperty('capability_token')
  expect(diagnosisRunView(internalDetail)).not.toHaveProperty('chain_of_thought')
})
```

- [ ] 运行测试确认失败。
- [ ] API 支持人工创建、列表、详情与健康摘要；页面只展示状态、已确认事实、待验证假设、引用与人工下一步，运行中仅轮询平台 API。
- [ ] 添加跨运行、跨环境、写操作尝试和 Dify 超时的端到端验证；记录真实 Dify 沙箱结果，未完成则明确写为缺口。
- [ ] 运行 `II_TEST_DATABASE_URL='mysql+pymysql://root:root@127.0.0.1:3307/mysql' ./scripts/verify-backend.sh`、`cd frontend && npm test -- --run && npm run build`。
- [ ] 提交：`git commit -m "feat: 提供平台主控的诊断入口与报告页面"`。

## 自检

- 六项任务覆盖平台控制、短期凭证、三项只读工具、审批知识、Dify Worker、报告校验、用户入口、失败退化和全量验证。
- 没有直接监控查询、Dify 业务写入或未定义的自由工具。
