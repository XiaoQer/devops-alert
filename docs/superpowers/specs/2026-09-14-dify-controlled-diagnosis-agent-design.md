# Dify 受控 Incident 诊断 Agent 设计

## 决策

采用“平台主控、Dify 受控执行”。Incident 平台保留事实、权限、证据、知识、审计与可信报告发布权；Dify 仅作为一个固定 Workflow 的模型编排和工具调用执行面。

不采用“Dify 主控”。Dify 的会话、工作流日志和知识库不能替代 Incident 状态机、EvidenceRun 快照、环境隔离、审计记录或平台权限。

不采用第一期直接自行开发模型工具循环。平台先通过适配器调用 Dify Workflow，保留以后替换为直连模型的边界。

## 运行关系

```text
操作员
  ↓ 手动开始分析
Incident API
  ↓ 同一事务
DiagnosisRun + 安全输入快照 + 持久化任务
  ↓
Diagnosis Worker ──固定、阻塞调用──> Dify Incident Diagnosis Workflow
                                      ↓ 只读工具调用
                              平台诊断工具 API / 平台知识检索 API
                                      ↓
                              结构化 JSON 草案
  ↓
平台引用与事实校验
  ├─ 通过：可信 DiagnosisReport
  └─ 不通过：REVIEW_REQUIRED + 有界审计
```

## 分层职责

| 层 | 职责 | 禁止项 |
| --- | --- | --- |
| Incident 平台 | 状态机、快照、权限、报告校验、审计、页面 | 将生产凭据交给 Dify |
| Knowledge 服务 | 受审批知识的版本、检索、范围过滤、引用 | 被 Dify 直接写入或管理 |
| Dify Workflow | 固定提示词、模型调用、有限工具编排、JSON 草案 | 业务写入、事实裁决、直接取证 |
| Dify 工具 | 读取诊断快照、检索知识、读取单项证据 | 任意查询、文件系统、Shell、写操作 |

## 输入快照

每次诊断都先冻结输入，以便重放和审计：

- Incident 编号、状态、环境、服务与严重级别；
- 关联 Alert 的安全投影；
- 选定 EvidenceRun、EvidenceItem、中文监控事实、来源状态和时间窗口；
- 允许的知识范围：环境、服务、Alertname 与通用知识；
- 契约、提示词和工具集合版本。

快照是 DiagnosisRun 的输入真相。诊断期间新到达的 Alert 或新产生的证据不改变当前运行；操作员可创建新运行获取新快照。

## Dify 工作流

固定 Workflow 名称为 `Incident Diagnosis`。仅允许以下顺序：

1. 调用 `get_diagnosis_snapshot`；
2. 基于快照提出最多三个知识检索问题；
3. 调用 `search_incident_knowledge`；
4. 在必要时调用 `get_evidence_detail`；
5. 输出版本化 JSON。

Workflow 不能直接触发下一次取证。它若认为证据不足，只能在 `unknowns` 中说明缺口，并在 `suggested_human_actions` 提出人工应做的下一步。

## 可信报告

平台将 Dify 返回的 JSON 解析为候选报告，依次校验：

1. 输出结构、长度和版本；
2. 每个引用是否属于本次快照或已保存的工具回执；
3. 已确认事实是否可由引用的确定性证据逐条支持；
4. 假设是否明确标为待验证；
5. 建议是否仅面向人工且不包含可执行命令、写入意图或未授权操作。

任何一项失败，报告不进入 `REPORT_READY`。这不是模型失败的掩盖；页面会说明“AI 输出与已确认事实不一致，未发布为可信报告”，并允许有权限的操作员查看有限原始草案与失败码。

## 故障语义

| 情况 | DiagnosisRun 结果 | Incident 影响 |
| --- | --- | --- |
| Dify 未配置 | FAILED / `dify_not_configured` | 无影响，人工仍可处置 |
| Dify 超时或暂时不可用 | FAILED，可有界重试 | 无影响 |
| 工具权限拒绝 | REVIEW_REQUIRED | 无影响，记录审计 |
| 引用不存在或事实矛盾 | REVIEW_REQUIRED | 不发布可信报告 |
| 知识库不可用 | 可继续仅基于证据，报告标记知识不可用 | 无影响 |
| 证据不足 | REPORT_READY，明确列入未知项 | 不把未知写成根因 |

## 第一阶段交付顺序

1. 诊断领域模型、输入快照、任务租约与数据库迁移；
2. Dify 配置与隔离的 HTTP 适配器；
3. 三个只读工具的能力令牌与审计；
4. Qdrant、Docling、嵌入 Worker 和最小知识库目录；
5. 报告结构校验、引用校验与可信报告持久化；
6. API 与 Incident 页面；
7. 自托管 Dify Workflow 沙箱联调。

第一阶段所有诊断均由人工启动；自动诊断、补充取证和自动修复需要后续独立规格。
