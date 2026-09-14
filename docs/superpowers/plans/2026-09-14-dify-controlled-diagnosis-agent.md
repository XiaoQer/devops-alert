# Dify 受控 Incident 诊断 Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. 本项目明确禁止子 Agent，所有任务在当前会话、`main` 分支顺序执行。

**Goal:** 在不削弱 Incident、证据和权限边界的前提下，以 Dify 固定 Workflow 为受控执行面，提供可审计、可引用、可人工复核的 Incident AI 诊断能力。

**Architecture:** 平台在 MySQL 中创建不可变 DiagnosisRun 输入快照与持久化任务；Diagnosis Worker 通过独立 Dify 适配器调用固定 Workflow。Dify 只能借一次性能力令牌调用平台的三个只读工具。平台验证所有返回引用和事实约束，只有通过后才保存并展示可信报告。知识文档的目录、版本、审批和审计保存在 MySQL，Qdrant 仅存向量索引。

**Tech Stack:** Python 3.13、FastAPI、Pydantic、SQLAlchemy、Alembic、MySQL 8.4、Vue 3、Vitest、Qdrant、Docling、bge-m3、本地 Dify Docker 部署。

**Spec:** `specs/active/dify-controlled-diagnosis-agent.md`；`docs/superpowers/specs/2026-09-14-dify-controlled-diagnosis-agent-design.md`

## Global Constraints

- 所有变更直接提交 `main`，不创建分支、worktree 或子 Agent；
- 绝不复用运行时未访问的历史 `diagnosis_runs` 表，使用新的 `incident_diagnosis_*` 物理表与 `drun_`、`dtask_`、`dreport_`、`dtool_` ID；
- 不得让 Dify、模型或知识库直连 MySQL、Prometheus、Elasticsearch、SkyWalking、飞书、Kubernetes 或故障注入平台；
- `scenario_id`、`scenario_version`、`experiment_id`、注入动作、标准答案、原始 Webhook 与 Secret 不得进入快照、工具、知识、提示词、报告、日志或 API；
- Dify API Key、工具服务凭据和能力令牌只从环境读取，能力令牌不得持久化正文、记录日志或返回浏览器；
- 所有写操作要求现有 Bearer Token、期望版本和幂等键；所有外部调用有固定地址、TLS、90 秒总超时、响应大小上限和有界重试；
- 第一阶段仅人工启动；不自动诊断、不自动取证、不自动通知、不自动修复；
- 任何 AI 结论必须区分已确认事实与待验证假设；禁止展示思维链或把模型输出作为平台事实覆盖 Alert/Incident/Evidence；
- 每个任务先写失败测试、观察失败、实现最小改动、运行目标测试并单独提交；全部任务结束后运行 `./scripts/verify-backend.sh`、`npm test -- --run`、`npm run build` 和真实 Dify 沙箱验证。

---

## 文件结构

| 文件 | 责任 |
| --- | --- |
| `backend/src/incident_intelligence/domain/diagnosis.py` | DiagnosisRun、快照、报告、引用与状态机不可变领域模型 |
| `backend/src/incident_intelligence/persistence/diagnosis_repository.py` | MySQL 诊断运行、任务、快照、工具回执、报告与操作幂等仓储 |
| `backend/src/incident_intelligence/services/incident_diagnosis.py` | 人工启动、读取、幂等、输入快照创建服务 |
| `backend/src/incident_intelligence/services/diagnosis_execution.py` | Worker 执行、Dify 调用、报告验证和重试策略 |
| `backend/src/incident_intelligence/services/knowledge_base.py` | 知识目录、审批、范围过滤、导入任务与受控检索 |
| `backend/src/incident_intelligence/services/diagnosis_tools.py` | 能力令牌校验和三项 Dify 只读工具服务 |
| `backend/src/incident_intelligence/adapters/dify.py` | Dify 阻塞 Workflow HTTP 客户端与稳定错误映射 |
| `backend/src/incident_intelligence/adapters/qdrant.py` | Qdrant 最小向量索引与混合检索适配器 |
| `backend/src/incident_intelligence/api/routes/incident_diagnosis.py` | 诊断运行与报告 API |
| `backend/src/incident_intelligence/api/routes/diagnosis_tools.py` | 仅供 Dify 服务身份调用的工具 API |
| `backend/src/incident_intelligence/api/routes/knowledge_documents.py` | 知识文档管理 API |
| `frontend/src/components/IncidentDiagnosis.vue` | Incident 详情的智能分析分区 |
| `frontend/src/components/KnowledgeBaseManager.vue` | 文档导入、审批、版本和索引状态页面 |
| `frontend/src/api/incidentDiagnosis.js` | 诊断 API 客户端 |
| `frontend/src/composables/useIncidentDiagnosis.js` | 诊断读取、创建和运行中轮询 |
| `frontend/src/presentation/diagnosisView.js` | 状态、引用与中文安全展示投影 |

### Task 1: 诊断领域状态机与报告契约

**Files:**
- Create: `backend/src/incident_intelligence/domain/diagnosis.py`
- Create: `backend/tests/unit/domain/test_diagnosis.py`
- Modify: `backend/src/incident_intelligence/ids.py`

**Interfaces:**
- Produces `DiagnosisRun`, `DiagnosisSnapshot`, `DiagnosisReport`, `DiagnosisReference`；
- Produces `validate_candidate_report(snapshot, candidate) -> ReportValidationResult`；
- Adds `drun`、`dtask`、`dreport`、`dtool`、`dtoken` ID 前缀。

- [ ] **Step 1: 写失败测试**

```python
def test_report_rejects_confirmed_fact_without_matching_evidence_reference() -> None:
    snapshot = diagnosis_snapshot(evidence_ids=("evitem_" + "1" * 32,))
    result = validate_candidate_report(
        snapshot,
        {"confirmed_facts": [{"text": "数据库锁等待升高", "evidence_ids": []}],
         "hypotheses": [], "evidence_references": [], "knowledge_references": [],
         "unknowns": [], "suggested_human_actions": []},
    )
    assert result.accepted is False
    assert result.reason_code == "diagnosis_fact_reference_missing"
```

- [ ] **Step 2: 运行失败测试**

Run: `cd backend && pytest tests/unit/domain/test_diagnosis.py -q`

Expected: FAIL，`incident_intelligence.domain.diagnosis` 不存在。

- [ ] **Step 3: 实现最小领域模型**

```python
DiagnosisRunState = Literal["QUEUED", "RUNNING", "REPORT_READY", "REVIEW_REQUIRED", "FAILED"]

class DiagnosisReference(_FrozenModel):
    kind: Literal["EVIDENCE", "KNOWLEDGE"]
    target_id: str
    content_hash: str

class DiagnosisReport(_FrozenModel):
    confirmed_facts: tuple[ConfirmedFact, ...]
    hypotheses: tuple[Hypothesis, ...]
    references: tuple[DiagnosisReference, ...]
    unknowns: tuple[str, ...]
    suggested_human_actions: tuple[str, ...]
```

限制数组最多 10 项、文本最多 1,000 字、总 JSON 最多 32 KB；`ConfirmedFact` 至少一个 Evidence 引用，`Hypothesis` 至少一个 Evidence 或 Knowledge 引用且 `verification_required=True`。拒绝命令行、URL、密钥模式和不属于快照的 ID。

- [ ] **Step 4: 运行目标测试**

Run: `cd backend && pytest tests/unit/domain/test_diagnosis.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/src/incident_intelligence/domain/diagnosis.py backend/src/incident_intelligence/ids.py backend/tests/unit/domain/test_diagnosis.py
git commit -m "feat: 定义诊断运行与报告契约"
```

### Task 2: MySQL 诊断快照、任务与审计持久化

**Files:**
- Create: `backend/migrations/versions/0020_controlled_dify_diagnosis.py`
- Create: `backend/src/incident_intelligence/persistence/diagnosis_repository.py`
- Modify: `backend/src/incident_intelligence/persistence/models.py`
- Modify: `backend/src/incident_intelligence/persistence/unit_of_work.py`
- Create: `backend/tests/integration/persistence/test_diagnosis_schema.py`
- Create: `backend/tests/integration/services/test_diagnosis_repository.py`

**Interfaces:**
- Consumes Task 1 的领域模型；
- Produces `DiagnosisRunRepository`、`DiagnosisTaskRepository`、`DiagnosisOperationRepository`；
- 在单个事务中保存 `DiagnosisRun + DiagnosisSnapshot + DiagnosisTask`。

- [ ] **Step 1: 写迁移与并发失败测试**

```python
def test_only_one_active_diagnosis_run_per_incident(session) -> None:
    insert_run(session, state="QUEUED", active_slot=1)
    with pytest.raises(IntegrityError):
        insert_run(session, state="RUNNING", active_slot=1)

def test_snapshot_is_immutable_and_has_no_secret_columns(session) -> None:
    columns = inspect(session.bind).get_columns("incident_diagnosis_snapshots")
    assert {"incident_snapshot", "evidence_snapshot", "scope_snapshot"} <= {c["name"] for c in columns}
    assert all("token" not in c["name"] and "secret" not in c["name"] for c in columns)
```

- [ ] **Step 2: 运行失败测试**

Run: `cd backend && pytest tests/integration/persistence/test_diagnosis_schema.py -q`

Expected: FAIL，迁移和表不存在。

- [ ] **Step 3: 实现新物理表和仓储**

创建 `incident_diagnosis_runs`、`incident_diagnosis_snapshots`、`incident_diagnosis_tasks`、`incident_diagnosis_tool_receipts`、`incident_diagnosis_reports`、`incident_diagnosis_operations`。约束包括：

```python
UniqueConstraint("incident_id", "active_slot", name="incident_active_diagnosis_run")
CheckConstraint("state IN ('QUEUED','RUNNING','REPORT_READY','REVIEW_REQUIRED','FAILED')")
CheckConstraint("attempt_count BETWEEN 0 AND 3")
CheckConstraint("char_length(idempotency_key_hash) = 64")
```

`active_slot=1` 仅用于 `QUEUED/RUNNING`；报告和工具回执用 `diagnosis_run_id + stable_key` 唯一约束。仓储沿用 `EvidenceTaskRepository` 的 `list_due/requeue_expired/claim_due/complete/fail/reschedule` 租约语义。

- [ ] **Step 4: 运行迁移和仓储测试**

Run: `cd backend && pytest tests/integration/persistence/test_diagnosis_schema.py tests/integration/services/test_diagnosis_repository.py -q`

Expected: PASS，并执行升级、降级、升级验证。

- [ ] **Step 5: 提交**

```bash
git add backend/migrations/versions/0020_controlled_dify_diagnosis.py backend/src/incident_intelligence/persistence/models.py backend/src/incident_intelligence/persistence/diagnosis_repository.py backend/src/incident_intelligence/persistence/unit_of_work.py backend/tests/integration/persistence/test_diagnosis_schema.py backend/tests/integration/services/test_diagnosis_repository.py
git commit -m "feat: 持久化受控诊断运行"
```

### Task 3: 人工启动诊断与不可变输入快照

**Files:**
- Create: `backend/src/incident_intelligence/services/incident_diagnosis.py`
- Create: `backend/tests/unit/services/test_incident_diagnosis.py`
- Create: `backend/tests/integration/services/test_incident_diagnosis.py`

**Interfaces:**
- Produces `IncidentDiagnosisService.request_manual(incident_id, evidence_run_id, actor, idempotency_key, request_id)`；
- Produces `list_runs`、`get_run`；
- 读取已有 `IncidentRepository`、`EvidenceRunRepository`、`AlertRepository`，不修改它们。

- [ ] **Step 1: 写失败测试**

```python
def test_manual_diagnosis_freezes_selected_evidence_run_and_alert_facts() -> None:
    result = service.request_manual(INCIDENT_ID, EVIDENCE_RUN_ID, actor="operator", idempotency_key="k", request_id="req_1")
    assert result.replayed is False
    assert result.run.state == "QUEUED"
    assert repository.snapshot(result.run.id).evidence_run_id == EVIDENCE_RUN_ID
    assert repository.snapshot(result.run.id).incident_snapshot["environment"] == "staging"

def test_second_active_run_is_rejected() -> None:
    service.request_manual(INCIDENT_ID, EVIDENCE_RUN_ID, actor="operator", idempotency_key="a", request_id="req_1")
    with pytest.raises(DiagnosisRunAlreadyActive):
        service.request_manual(INCIDENT_ID, EVIDENCE_RUN_ID, actor="operator", idempotency_key="b", request_id="req_2")
```

- [ ] **Step 2: 运行失败测试**

Run: `cd backend && pytest tests/unit/services/test_incident_diagnosis.py tests/integration/services/test_incident_diagnosis.py -q`

Expected: FAIL，服务不存在。

- [ ] **Step 3: 实现服务**

快照仅包含现有安全投影、最多 500 条 Alert、100 个 EvidenceItem、五条监控事实和受限的 `environment/service/alert_names`。`evidence_run_id` 必须属于 Incident 且状态为 `SUCCEEDED/PARTIAL`；不能从 Dify 输入推导服务、环境或任何实验字段。操作幂等键按 `incident:{id}:diagnosis` 哈希，与已有 Evidence 操作模式一致。

- [ ] **Step 4: 运行目标测试**

Run: `cd backend && pytest tests/unit/services/test_incident_diagnosis.py tests/integration/services/test_incident_diagnosis.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/src/incident_intelligence/services/incident_diagnosis.py backend/tests/unit/services/test_incident_diagnosis.py backend/tests/integration/services/test_incident_diagnosis.py
git commit -m "feat: 支持人工创建诊断快照"
```

### Task 4: 知识库目录、审批与 Qdrant 最小索引

**Files:**
- Modify: `compose.yaml`
- Modify: `backend/pyproject.toml`
- Create: `backend/src/incident_intelligence/domain/knowledge.py`
- Create: `backend/src/incident_intelligence/persistence/knowledge_repository.py`
- Create: `backend/src/incident_intelligence/adapters/qdrant.py`
- Create: `backend/src/incident_intelligence/services/knowledge_base.py`
- Create: `backend/migrations/versions/0021_incident_knowledge_base.py`
- Create: `backend/tests/unit/services/test_knowledge_base.py`
- Create: `backend/tests/unit/adapters/test_qdrant.py`
- Create: `backend/tests/integration/persistence/test_knowledge_schema.py`

**Interfaces:**
- Produces `KnowledgeDocument`、`KnowledgeChunk`、`KnowledgeSearchResult`；
- Produces `KnowledgeBaseService.search(scope, query, limit=5)`；
- `scope` 固定为 `environment`、`service_name`、`alert_names`，不接受 Dify 传入的自由范围。

- [ ] **Step 1: 写范围过滤失败测试**

```python
def test_search_excludes_draft_and_wrong_environment_chunks() -> None:
    results = service.search(scope=scope(environment="staging"), query="MySQL 行锁等待", limit=5)
    assert [result.chunk_id for result in results] == [APPROVED_STAGING_CHUNK]

def test_qdrant_payload_contains_version_hash_and_scope_fields() -> None:
    payload = to_qdrant_payload(chunk)
    assert payload["status"] == "APPROVED"
    assert payload["content_hash"] == chunk.content_hash
    assert payload["environment"] == ["staging"]
```

- [ ] **Step 2: 运行失败测试**

Run: `cd backend && pytest tests/unit/services/test_knowledge_base.py tests/unit/adapters/test_qdrant.py -q`

Expected: FAIL，知识领域与 Qdrant 适配器不存在。

- [ ] **Step 3: 实现最小知识库**

`compose.yaml` 增加仅绑定本机网络的 Qdrant 与持久卷。MySQL 创建 `knowledge_documents`、`knowledge_document_versions`、`knowledge_chunks`、`knowledge_import_tasks`；文档状态只允许 `DRAFT/APPROVED/RETIRED`。Qdrant payload 只保存 `chunk_id/document_version_id/content_hash/status/environments/services/alert_names/document_type`，原文仍由平台目录持有。第一批只支持 Markdown 与 UTF-8 文本导入；Docling、PDF/Office 解析和 bge-m3 嵌入进程留给下一任务，避免把解析、索引与权限边界混在一起。

- [ ] **Step 4: 运行目标测试**

Run: `cd backend && pytest tests/unit/services/test_knowledge_base.py tests/unit/adapters/test_qdrant.py tests/integration/persistence/test_knowledge_schema.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add compose.yaml backend/pyproject.toml backend/src/incident_intelligence/domain/knowledge.py backend/src/incident_intelligence/persistence/knowledge_repository.py backend/src/incident_intelligence/adapters/qdrant.py backend/src/incident_intelligence/services/knowledge_base.py backend/migrations/versions/0021_incident_knowledge_base.py backend/tests/unit/services/test_knowledge_base.py backend/tests/unit/adapters/test_qdrant.py backend/tests/integration/persistence/test_knowledge_schema.py
git commit -m "feat: 增加受审批 Incident 知识库"
```

### Task 5: 文档解析、向量化与可重放索引任务

**Files:**
- Create: `backend/src/incident_intelligence/services/knowledge_ingestion.py`
- Create: `backend/src/incident_intelligence/services/knowledge_ingestion_runner.py`
- Create: `backend/src/incident_intelligence/adapters/document_parser.py`
- Create: `backend/src/incident_intelligence/adapters/embedding.py`
- Create: `backend/tests/unit/services/test_knowledge_ingestion.py`
- Create: `backend/tests/unit/adapters/test_document_parser.py`
- Create: `backend/tests/integration/services/test_knowledge_ingestion.py`
- Modify: `backend/src/incident_intelligence/main.py`
- Modify: `backend/src/incident_intelligence/settings.py`

**Interfaces:**
- Produces `KnowledgeIngestionService.process(task_id)`；
- Produces `DocumentParser.parse(path) -> tuple[ParsedSection, ...]`；
- Produces `EmbeddingClient.embed(texts) -> tuple[tuple[float, ...], ...]`；
- Worker 复用任务租约模式，单任务最大 10 MB、500 段、每段 800 token。

- [ ] **Step 1: 写失败测试**

```python
def test_ingestion_creates_heading_aware_chunks_with_stable_hashes() -> None:
    outcome = service.process(TASK_ID)
    assert outcome.state == "SUCCEEDED"
    assert repository.list_chunks(DOCUMENT_VERSION_ID)[0].heading == "行锁等待排查"
    assert repository.list_chunks(DOCUMENT_VERSION_ID)[0].content_hash == sha256(expected_text.encode()).hexdigest()

def test_reindexing_same_version_does_not_duplicate_qdrant_points() -> None:
    service.process(TASK_ID)
    service.process(retry_task_id)
    assert vector_store.upsert_calls == 1
```

- [ ] **Step 2: 运行失败测试**

Run: `cd backend && pytest tests/unit/services/test_knowledge_ingestion.py tests/integration/services/test_knowledge_ingestion.py -q`

Expected: FAIL，导入服务不存在。

- [ ] **Step 3: 实现解析和索引**

先通过 `DocumentParser` 隔离 Docling；Markdown/TXT 使用本地解析器，PDF/DOCX/XLSX 由 Docling 转为结构化文本。嵌入通过 `EmbeddingClient` 隔离 bge-m3 服务；所有模型地址和凭据均从环境读取。按标题边界切分，目标 400–800 token、最多 80 token 重叠；旧版本或 `RETIRED` 文档从 Qdrant 删除。Worker 加入应用生命周期，但知识索引失败不得影响诊断读取既有已审批版本。

- [ ] **Step 4: 运行目标测试**

Run: `cd backend && pytest tests/unit/services/test_knowledge_ingestion.py tests/unit/adapters/test_document_parser.py tests/integration/services/test_knowledge_ingestion.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/src/incident_intelligence/services/knowledge_ingestion.py backend/src/incident_intelligence/services/knowledge_ingestion_runner.py backend/src/incident_intelligence/adapters/document_parser.py backend/src/incident_intelligence/adapters/embedding.py backend/src/incident_intelligence/main.py backend/src/incident_intelligence/settings.py backend/tests/unit/services/test_knowledge_ingestion.py backend/tests/unit/adapters/test_document_parser.py backend/tests/integration/services/test_knowledge_ingestion.py
git commit -m "feat: 增加知识文档导入与索引任务"
```

### Task 6: 能力令牌与 Dify 三项只读工具

**Files:**
- Create: `backend/src/incident_intelligence/services/diagnosis_tools.py`
- Create: `backend/src/incident_intelligence/api/routes/diagnosis_tools.py`
- Create: `backend/src/incident_intelligence/api/schemas/diagnosis_tools.py`
- Create: `backend/tests/unit/services/test_diagnosis_tools.py`
- Create: `backend/tests/api/test_diagnosis_tools.py`
- Modify: `backend/src/incident_intelligence/api/router.py`
- Modify: `backend/src/incident_intelligence/api/dependencies.py`

**Interfaces:**
- Produces `DiagnosisCapabilityIssuer.issue(run_id, expires_at) -> str`，令牌不落库；
- Produces `DiagnosisToolService.get_snapshot`、`search_knowledge`、`get_evidence_detail`；
- 路由只接受 Dify 服务认证与能力令牌，不使用用户 Bearer Token。

- [ ] **Step 1: 写越权失败测试**

```python
def test_capability_token_cannot_read_a_different_diagnosis_run(client) -> None:
    response = client.get(f"/api/v1/diagnosis-tools/runs/{OTHER_RUN}/snapshot", headers=dify_headers(token_for(RUN_ID)))
    assert response.status_code == 403
    assert response.json()["code"] == "diagnosis_capability_scope_denied"

def test_knowledge_tool_ignores_attempt_to_expand_environment(client) -> None:
    response = client.post(f"/api/v1/diagnosis-tools/runs/{RUN_ID}/knowledge-search", json={"query": "密码", "environment": "production"}, headers=dify_headers(token_for(RUN_ID)))
    assert all(item["environment"] != "production" for item in response.json()["items"])
```

- [ ] **Step 2: 运行失败测试**

Run: `cd backend && pytest tests/unit/services/test_diagnosis_tools.py tests/api/test_diagnosis_tools.py -q`

Expected: FAIL，工具服务和路由不存在。

- [ ] **Step 3: 实现受控工具**

使用 HMAC 签发含 `run_id`、`expires_at`、`audience=dify-tools`、随机 nonce 的短令牌；校验通过后只从快照读取范围。返回固定 Pydantic 模型和硬上限，记录 `dtool_` 回执、请求参数指纹、结果内容指纹、截断标记与稳定错误码；不记录令牌和原始请求头。知识工具忽略 `environment/service/alertname` 等外来范围参数。

- [ ] **Step 4: 运行目标测试**

Run: `cd backend && pytest tests/unit/services/test_diagnosis_tools.py tests/api/test_diagnosis_tools.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/src/incident_intelligence/services/diagnosis_tools.py backend/src/incident_intelligence/api/routes/diagnosis_tools.py backend/src/incident_intelligence/api/schemas/diagnosis_tools.py backend/src/incident_intelligence/api/router.py backend/src/incident_intelligence/api/dependencies.py backend/tests/unit/services/test_diagnosis_tools.py backend/tests/api/test_diagnosis_tools.py
git commit -m "feat: 提供受控 Dify 诊断工具"
```

### Task 7: Dify 适配器、报告校验与诊断 Worker

**Files:**
- Create: `backend/src/incident_intelligence/adapters/dify.py`
- Create: `backend/src/incident_intelligence/services/diagnosis_execution.py`
- Create: `backend/src/incident_intelligence/services/diagnosis_runner.py`
- Create: `backend/tests/unit/adapters/test_dify.py`
- Create: `backend/tests/unit/services/test_diagnosis_execution.py`
- Create: `backend/tests/integration/services/test_diagnosis_execution.py`
- Modify: `backend/src/incident_intelligence/main.py`
- Modify: `backend/src/incident_intelligence/settings.py`

**Interfaces:**
- Produces `DifyDiagnosisClient.run_workflow(run_id, capability_token) -> DifyCandidateReport`；
- Produces `DiagnosisExecutionService.process(task_id)`；
- Dify 错误统一为 `dify_not_configured`、`dify_timeout`、`dify_rate_limited`、`dify_unauthorized`、`dify_protocol_invalid`、`dify_unavailable`。

- [ ] **Step 1: 写失败测试**

```python
def test_invalid_evidence_reference_moves_run_to_review_required() -> None:
    client.reply({"confirmed_facts": [{"text": "已确认", "evidence_ids": ["evitem_missing"]}], "hypotheses": [], "evidence_references": [], "knowledge_references": [], "unknowns": [], "suggested_human_actions": []})
    result = service.process(TASK_ID)
    assert result.outcome == "REVIEW_REQUIRED"
    assert repository.get_run(RUN_ID).state == "REVIEW_REQUIRED"

def test_timeout_reschedules_without_changing_incident_state() -> None:
    client.raise_timeout()
    assert service.process(TASK_ID).outcome == "RETRY_SCHEDULED"
    assert incident_repository.get(INCIDENT_ID).state == "OPEN"
```

- [ ] **Step 2: 运行失败测试**

Run: `cd backend && pytest tests/unit/adapters/test_dify.py tests/unit/services/test_diagnosis_execution.py tests/integration/services/test_diagnosis_execution.py -q`

Expected: FAIL，Dify 适配器与执行服务不存在。

- [ ] **Step 3: 实现适配器和 Worker**

`DifyDiagnosisClient` 只向配置的固定 Workflow 发起一次阻塞请求，连接与总超时合计不超过 90 秒，响应正文不超过 64 KB；不得透传数据库或监控凭据。执行服务先领取租约，签发能力令牌，调用客户端，校验候选报告，保存工具回执与报告；可恢复错误按 5 秒、30 秒、2 分钟退避，第三次失败进入 `FAILED`。校验失败只进入 `REVIEW_REQUIRED`，不重试模型调用。主应用按配置启动独立诊断 Runner。

- [ ] **Step 4: 运行目标测试**

Run: `cd backend && pytest tests/unit/adapters/test_dify.py tests/unit/services/test_diagnosis_execution.py tests/integration/services/test_diagnosis_execution.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/src/incident_intelligence/adapters/dify.py backend/src/incident_intelligence/services/diagnosis_execution.py backend/src/incident_intelligence/services/diagnosis_runner.py backend/src/incident_intelligence/main.py backend/src/incident_intelligence/settings.py backend/tests/unit/adapters/test_dify.py backend/tests/unit/services/test_diagnosis_execution.py backend/tests/integration/services/test_diagnosis_execution.py
git commit -m "feat: 执行受控 Dify 诊断任务"
```

### Task 8: 诊断、知识库管理 API 与健康摘要

**Files:**
- Create: `backend/src/incident_intelligence/api/routes/incident_diagnosis.py`
- Create: `backend/src/incident_intelligence/api/routes/knowledge_documents.py`
- Create: `backend/src/incident_intelligence/api/schemas/incident_diagnosis.py`
- Create: `backend/src/incident_intelligence/api/schemas/knowledge_documents.py`
- Modify: `backend/src/incident_intelligence/api/dependencies.py`
- Modify: `backend/src/incident_intelligence/api/router.py`
- Modify: `backend/src/incident_intelligence/api/routes/health.py`
- Create: `backend/tests/api/test_incident_diagnosis.py`
- Create: `backend/tests/api/test_knowledge_documents.py`
- Modify: `backend/tests/api/test_health.py`

**Interfaces:**
- Produces `GET/POST /api/v1/incidents/{incident_id}/diagnosis-runs`；
- Produces `GET /api/v1/incidents/{incident_id}/diagnosis-runs/{run_id}`；
- Produces受保护的知识文档创建、审批、停用、版本读取 API；
- `/health/ready` 增加有界诊断任务、Dify 配置与知识索引摘要。

- [ ] **Step 1: 写失败测试**

```python
def test_create_diagnosis_requires_bearer_version_and_idempotency(client) -> None:
    assert client.post(f"/api/v1/incidents/{INCIDENT_ID}/diagnosis-runs", json={"evidence_run_id": EVIDENCE_RUN_ID}).status_code == 401

def test_diagnosis_detail_never_returns_capability_token_or_raw_prompt(client) -> None:
    response = client.get(f"/api/v1/incidents/{INCIDENT_ID}/diagnosis-runs/{RUN_ID}", headers=actor_headers())
    assert "capability" not in response.text.lower()
    assert "prompt" not in response.text.lower()
```

- [ ] **Step 2: 运行失败测试**

Run: `cd backend && pytest tests/api/test_incident_diagnosis.py tests/api/test_knowledge_documents.py tests/api/test_health.py -q`

Expected: FAIL，诊断和知识库 API 未注册。

- [ ] **Step 3: 实现路由与安全投影**

所有 API 错误映射为稳定中文消息；诊断详情只返回报告、安全引用、运行状态、错误码与截断标记。知识审批版本必须乐观并发，删除改为 `RETIRED`，且只移除向量索引。健康摘要只返回待处理、租约中、失败数量、最近成功/错误码及 Dify 是否配置，禁止返回 Workflow ID、基地址、Token、路径或请求内容。

- [ ] **Step 4: 运行目标测试**

Run: `cd backend && pytest tests/api/test_incident_diagnosis.py tests/api/test_knowledge_documents.py tests/api/test_health.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/src/incident_intelligence/api/routes/incident_diagnosis.py backend/src/incident_intelligence/api/routes/knowledge_documents.py backend/src/incident_intelligence/api/schemas/incident_diagnosis.py backend/src/incident_intelligence/api/schemas/knowledge_documents.py backend/src/incident_intelligence/api/dependencies.py backend/src/incident_intelligence/api/router.py backend/src/incident_intelligence/api/routes/health.py backend/tests/api/test_incident_diagnosis.py backend/tests/api/test_knowledge_documents.py backend/tests/api/test_health.py
git commit -m "feat: 暴露诊断与知识库管理接口"
```

### Task 9: Incident 智能分析与知识库页面

**Files:**
- Create: `frontend/src/api/incidentDiagnosis.js`
- Create: `frontend/src/api/incidentDiagnosis.test.js`
- Create: `frontend/src/api/knowledgeDocuments.js`
- Create: `frontend/src/composables/useIncidentDiagnosis.js`
- Create: `frontend/src/composables/useIncidentDiagnosis.test.js`
- Create: `frontend/src/presentation/diagnosisView.js`
- Create: `frontend/src/presentation/diagnosisView.test.js`
- Create: `frontend/src/components/IncidentDiagnosis.vue`
- Create: `frontend/src/components/IncidentDiagnosis.test.js`
- Create: `frontend/src/components/KnowledgeBaseManager.vue`
- Create: `frontend/src/components/KnowledgeBaseManager.test.js`
- Modify: `frontend/src/components/IncidentDetail.vue`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- `useIncidentDiagnosis(incidentId)` 返回 `runs/detail/state/startDiagnosis`；
- `toDiagnosisDetailView` 返回中文状态、可信报告的五个分区和安全引用链接；
- 不保存或展示思维链、原始提示词、能力令牌、模型凭据。

- [ ] **Step 1: 写失败测试**

```javascript
it("只展示已校验报告并把假设明确标记为待验证", () => {
  const wrapper = mount(IncidentDiagnosis, { props: { incidentId: "inc_1", providedState: readyState() } });
  expect(wrapper.text()).toContain("已确认事实");
  expect(wrapper.text()).toContain("待验证假设");
  expect(wrapper.text()).not.toContain("内部推理");
});

it("审核前报告展示需要人工复核而不展示为可信结论", () => {
  expect(toDiagnosisDetailView(reviewRequiredRun()).reportVisible).toBe(false);
});
```

- [ ] **Step 2: 运行失败测试**

Run: `cd frontend && npm test -- --run src/components/IncidentDiagnosis.test.js src/presentation/diagnosisView.test.js`

Expected: FAIL，组件和展示投影不存在。

- [ ] **Step 3: 实现最小页面**

在 Incident 详情“监控取证”之后加入“智能分析”。默认显示最近一次诊断状态和“开始分析”；运行中每两秒刷新，最大一分钟后提示可手动刷新。`REPORT_READY` 只展示五个报告分区及可点击证据/知识引用；`REVIEW_REQUIRED` 仅显示“AI 输出未通过事实校验，未发布为可信报告”和稳定失败码；知识库页面只支持导入、查看索引、审批、停用和版本查看。

- [ ] **Step 4: 运行目标测试与构建**

Run: `cd frontend && npm test -- --run && npm run build`

Expected: PASS，生产构建成功。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/api/incidentDiagnosis.js frontend/src/api/incidentDiagnosis.test.js frontend/src/api/knowledgeDocuments.js frontend/src/composables/useIncidentDiagnosis.js frontend/src/composables/useIncidentDiagnosis.test.js frontend/src/presentation/diagnosisView.js frontend/src/presentation/diagnosisView.test.js frontend/src/components/IncidentDiagnosis.vue frontend/src/components/IncidentDiagnosis.test.js frontend/src/components/KnowledgeBaseManager.vue frontend/src/components/KnowledgeBaseManager.test.js frontend/src/components/IncidentDetail.vue frontend/src/App.vue frontend/src/styles.css
git commit -m "feat: 展示 Incident 智能分析与知识库"
```

### Task 10: Dify 沙箱联调、端到端验收与状态文档

**Files:**
- Create: `docs/verification/2026-09-14-dify-controlled-diagnosis-agent.md`
- Modify: `docs/product.md`
- Modify: `docs/architecture.md`
- Modify: `docs/current-state.md`
- Modify: `specs/active/dify-controlled-diagnosis-agent.md`

**Interfaces:**
- 消费前九项能力；
- 产出一份可重放的 Dify 沙箱验收记录，不记录 Token、提示词原文或敏感证据。

- [ ] **Step 1: 写端到端失败场景**

```python
def test_dify_sandbox_report_with_unknown_reference_is_not_published(api_client, dify_sandbox) -> None:
    run = start_diagnosis(api_client)
    dify_sandbox.reply_with_unknown_reference(run.id)
    wait_for_terminal_state(run.id)
    assert get_run(run.id).state == "REVIEW_REQUIRED"
    assert get_run(run.id).trusted_report is None
```

- [ ] **Step 2: 运行失败场景**

Run: `cd backend && pytest tests/integration/test_dify_diagnosis_sandbox.py -q`

Expected: FAIL，沙箱夹具与端到端场景不存在。

- [ ] **Step 3: 实现沙箱夹具与验收记录**

使用本地自托管 Dify 中的固定 `Incident Diagnosis` Workflow 和三个只读工具；导入一个审批通过的 MySQL Runbook，创建真实 Incident、完成 Prometheus 取证、人工启动诊断。验证可信报告引用真实 EvidenceItem 与知识片段；再注入一个未知引用并确认其进入 `REVIEW_REQUIRED`。将实际版本、命令、测试数量、健康状态、已知限制写入验证文档。

- [ ] **Step 4: 运行全量验证**

Run: `II_TEST_DATABASE_URL='mysql+pymysql://root:root@127.0.0.1:3307/mysql' ./scripts/verify-backend.sh`

Run: `cd frontend && npm test -- --run && npm run build`

Run: `git diff --check`

Expected: 后端格式、严格类型、迁移与全部测试通过；前端测试与构建通过；Dify 沙箱中合法报告发布、越权/无效引用拒绝；浏览器控制台无错误。

- [ ] **Step 5: 更新状态并提交**

将 `docs/product.md` 中“DiagnosisRun 和 AI 分析”从明确不做调整为本规格已实现的受控诊断边界；`docs/architecture.md` 写入 Diagnosis Worker、Dify 适配器、知识服务和数据流；`docs/current-state.md` 仅记录真实验收过的能力与 Dify/知识库部署限制。所有验收通过后把活跃规格移到 `specs/completed/`。

```bash
git add docs/verification/2026-09-14-dify-controlled-diagnosis-agent.md docs/product.md docs/architecture.md docs/current-state.md specs/active/dify-controlled-diagnosis-agent.md specs/completed/dify-controlled-diagnosis-agent.md
git commit -m "docs: 验收受控 Dify 诊断 Agent"
```

## 计划自检

- 规格覆盖：诊断状态机/快照（任务 1–3）、知识库（4–5）、能力令牌与工具隔离（6）、Dify 适配器与可信校验（7）、API/健康（8）、前端（9）、真实沙箱与文档（10）。
- 边界覆盖：无直接生产访问、无任意查询、无自动动作、无思维链、无实验身份、无 Secret，均在全局约束和任务 3、6、7、8、9 中有测试或实现步骤。
- 类型一致性：`DiagnosisRun`、`DiagnosisSnapshot`、`DiagnosisReport` 由任务 1 定义；任务 2–9 只消费这些名称；Dify 仅返回候选报告，平台验证后才创建 `DiagnosisReport`。
- 独立验收：每项任务均包含失败测试、目标测试和单独提交；最终任务执行迁移、全量验证与真实 Dify 沙箱验证。
