# 平台主控的 Dify Incident 诊断执行器

## 状态

实施中：已完成 DiagnosisRun 的领域契约、MySQL 运行/快照/任务/幂等持久化，以及基于既有 EvidenceRun 的人工输入快照；已提供诊断 API、受限快照/证据工具、能力令牌、本地演示 Worker 和 Incident 前端入口。真实 Dify 调用适配器、本地 Kubernetes Dify 实例、管理员、DeepSeek 模型供应商和固定 Workflow 已就绪；该 Workflow 已发布并通过静态检查，使用一次受限快照读取后交由 DeepSeek 输出草案，应用 API Key 仅配置于当前本地后端运行进程。按证据项受控读取、知识检索和真实沙箱联调尚未完成。架构以平台主程序为唯一控制面，Dify 仅为受控、无状态的 Workflow 执行器。

## 背景与目标

平台已经能够可靠地接收告警、生成 Incident，并将 Prometheus、Elasticsearch、SkyWalking 的版本化只读查询结果保存为 EvidenceRun 与 EvidenceItem。操作员下一步需要基于已保存证据和经过审批的运维知识进行辅助研判。

本规格把 Dify 定位为受控的 AI 工作流执行面，而不是 Incident、证据、知识、权限或审计的事实来源。平台持有所有事实、执行边界和最终报告；Dify 只能在平台给定的诊断上下文中调用受限只读工具并返回结构化草案。

## 范围

- 新建独立 DiagnosisRun、诊断任务、不可变输入快照、工具调用回执和诊断报告；
- Incident 页面支持人工启动一次诊断、查看运行状态和可信报告；
- 后端异步调用一个固定的 Dify Workflow；
- Dify Workflow 只能调用平台提供的诊断快照、知识检索、证据详情三种只读工具；
- 平台校验 Dify 的结构化输出、证据引用和知识引用，只有校验通过才发布可信报告；
- Dify 不可用时可重试且不阻断 Incident、取证、飞书通知或人工处置；
- 诊断运行、模型调用、工具调用和输出校验均有有界审计。

## 非目标

- Dify 直接访问 MySQL、Prometheus、Elasticsearch、SkyWalking、飞书或 Kubernetes；
- Dify 作为知识库、权限中心、Incident 状态机或审计系统；
- AI 自由生成监控查询、执行 Shell、变更配置、发送通知或自动修复；
- 自动启动诊断、自动追加取证、自动认定根因或自动解决 Incident；
- 多 Agent 协作、自由聊天记忆、跨 Incident 上下文复用；
- 把故障注入平台、`scenario_id`、实验动作、标准答案或任何 Secret 输入诊断。

## 设计

### 责任边界

```text
Incident 平台：事实、权限、证据快照、知识版本、诊断状态机、审计、报告发布
        ↓ 固定输入和受限能力令牌
Dify Workflow：固定提示词、受控工具编排、模型推理、结构化草案
        ↓ 原始结构化输出
Incident 平台：引用校验、可信报告或人工复核结果
```

平台不把数据库凭据、监控系统凭据、飞书凭据或任意生产访问权交给 Dify。Dify 也不写入平台业务表；所有业务写入均由平台 Diagnosis Worker 完成。

### DiagnosisRun 与状态机

新模型使用 `drun_`、`dtask_`、`dreport_`、`dtool_` 前缀，并使用新物理表，不能复用仓库中历史遗留且运行时未访问的 `diagnosis_runs` 等表。

DiagnosisRun 状态：

```text
QUEUED → RUNNING → REPORT_READY
                 ↘ REVIEW_REQUIRED
                 ↘ FAILED
```

- 操作员从未解决或已解决 Incident 的详情页手动启动；同一 Incident 最多一个活动 DiagnosisRun；
- 启动事务固定 Incident、关联 Alert、选定 EvidenceRun、EvidenceItem 与版本化知识检索范围，写入任务和不可变输入快照；
- `REPORT_READY` 表示输出通过全部平台校验；
- `REVIEW_REQUIRED` 表示 Dify 有返回但结构、引用或事实约束不满足，原始输出只向有权限操作员展示；
- `FAILED` 表示配置缺失、Dify 连接、超时、配额或不可恢复协议错误；
- 人工重新诊断必须产生新运行，旧快照、回执和报告永不覆盖。

### Dify 集成契约

平台配置只保存 Dify 基地址、启停状态与 Workflow 显示标识；Dify API Key 和平台能力密钥只从运行环境读取。Dify 的应用 API Key 已绑定目标 Workflow，因此不接受由请求方指定的任意 Workflow ID。连接检测调用固定无副作用接口，不发送 Incident 内容。

Diagnosis Worker 通过阻塞模式调用指定 Workflow，并传入：

- `diagnosis_run_id`；
- 一次性、短有效期、仅适用于该运行的能力令牌；
- 固定 JSON 输出契约版本。

每次调用最大 90 秒、最大一次 Dify 请求；临时连接、超时、5xx 或限流错误使用持久化租约按 5 秒、30 秒有界重试，最多三次尝试。调用协议由 `DifyDiagnosisClient` 隔离：固定调用 `/v1/workflows/run` 的阻塞模式，只接收版本化 `diagnosis_report` 输出；具体 Dify 版本仍必须完成真实兼容测试后锁定。

### Dify 只读工具

Dify Workflow 仅注册以下工具。每个工具均要求能力令牌，且平台从 DiagnosisRun 快照派生筛选条件，忽略 Dify 试图扩大范围的参数。

| 工具 | 作用 | 输出上限 |
| --- | --- | --- |
| `get_diagnosis_snapshot` | 读取固定的 Incident、Alert 和监控事实摘要 | 1 个快照，最多 40 KB |
| `search_incident_knowledge` | 在审批通过且符合环境、服务、Alertname 的知识中检索 | 每次最多 5 段、合计 20 KB、最多调用 3 次 |
| `get_evidence_detail` | 读取快照内某个 EvidenceItem 的标准摘要与受控明细 | 每次 1 项、最多 8 KB |

工具回执包含稳定 ID、内容指纹、来源版本和截断标记，并由平台保存。工具不存在写操作、任意 URL、任意查询文本、任意文件读取或跨环境检索。

### 知识检索

平台知识库独立于 Dify 管理。知识片段必须具有：文档 ID、片段 ID、内容指纹、版本、标题、文档类型、审批状态、环境、服务、Alertname、来源链接和更新时间。检索只接受 `APPROVED` 的片段，先执行环境/服务/告警名过滤，再执行有界混合检索；Dify 不能上传、修改或删除知识。

知识库基础设施在本规格的第一实施阶段完成最小可用版本：平台 MySQL 保存目录、版本和审计，Qdrant 保存向量索引，Docling 解析受支持的文档，本地嵌入模型生成向量。Dify 只能经 `search_incident_knowledge` 使用其结果。

### 输出与可信报告校验

Dify 只能返回版本化 JSON：

```json
{
  "confirmed_facts": [],
  "hypotheses": [],
  "evidence_references": [],
  "knowledge_references": [],
  "unknowns": [],
  "suggested_human_actions": []
}
```

- `confirmed_facts` 必须逐条引用固定快照内的 EvidenceItem，且只能复述对应的确定性事实；
- `hypotheses` 必须使用“待验证”语义，至少引用一条 EvidenceItem 或知识片段，不能写成已确认根因；
- 每个引用必须匹配该 DiagnosisRun 已保存的快照或工具回执 ID 与内容指纹；
- `suggested_human_actions` 只能是人工建议，禁止包含可执行命令、变更指令或自动化调用；
- 总输出、单字段、数组条数、工具回执和错误文本均有硬上限；
- 校验失败不发布可信报告，运行进入 `REVIEW_REQUIRED`，并保存稳定失败码及有界原始输出摘要。

### 页面与 API

- Incident 详情新增“智能分析”分区，默认只显示最近一次运行状态、人工“开始分析/重新分析”入口、可信报告和引用；
- 报告按“已确认事实、待验证假设、参考知识、尚不确定、建议人工下一步”展示；
- 不展示模型内部思维过程、Secret、任意工具凭据或无限原始内容；
- API 提供运行列表、单次详情、人工创建和 Dify 配置健康摘要；所有写操作使用 Bearer Token、期望版本和幂等键；
- 运行中页面轮询，失败仅显示稳定中文状态和可人工重试入口。

## 安全与恢复

- 所有 Dify、模型和知识库凭据仅从环境变量读取；
- 能力令牌为单次运行、短有效期、不可扩权的签名令牌，不进入日志、API 响应、数据库正文或 Dify 输出；
- Dify 网络调用使用固定允许地址、超时、响应大小和 TLS 配置；
- 诊断输入只使用平台既有的安全投影，不发送原始 Webhook、未脱敏日志、完整 Trace、Secret 或实验身份；
- Dify、知识库、模型或诊断任务失败不影响 Alert 接收、Incident 状态机、证据采集、飞书通知和人工处置；
- 所有诊断数据按 Incident 审计保留策略处理，支持查看、导出审计摘要和有界重试，不支持修改历史快照。

## 验收条件

- 平台可人工创建 DiagnosisRun，并在同一事务冻结唯一输入快照和持久化任务；
- 同一 Incident 不会并发执行两个诊断运行，幂等重放不重复调用 Dify；
- Dify 仅能使用三项只读工具，任何跨运行、跨环境、跨服务、任意查询和写操作请求均被拒绝并审计；
- Dify 不可用、超时、限流、认证失败和协议不兼容均安全退化为可理解状态；
- 可信报告的每项事实、假设和知识引用可回链到快照或工具回执；无效引用、超长输出、未标记假设或不符合事实约束的输出不得发布；
- 知识检索只返回已审批、适用当前 Incident 范围的内容，历史版本与内容指纹可复核；
- Agent 不可访问任何监控、数据库、飞书、Kubernetes 或故障注入系统的直接凭据；
- 前端不展示思维链，只展示状态、报告、引用和人工下一步；
- 后端统一验证、前端测试、生产构建和 Dify 沙箱兼容主流程均通过。

## 验证证据

- 待实施：领域状态机、快照边界、能力令牌、工具权限、报告校验、知识范围、Worker 租约和 API 的失败测试；
- 已完成：Dify 阻塞调用协议、认证/超时/限流/协议错误的适配器测试；
- 待实施：MySQL 迁移升降级、并发收敛、幂等重放和安全扫描；
- 待实施：前端运行状态、报告引用、失败降级和真实浏览器流程；
- 已完成：自托管 Dify 基础实例已部署于本机 `dify-system` 命名空间，Chart `0.39.0-rc1` / Dify `1.17.0`，入口为 `http://dify.localhost/`；管理员、DeepSeek 模型供应商和“Incident 受控诊断”Workflow 已创建并发布。其主链为开始节点、一次受限快照读取、DeepSeek LLM 和输出；静态检查通过，应用 API Key 仅配置于本地后端运行进程。
- 待实施：证据详情、知识检索和真实 DiagnosisRun 的端到端沙箱验证。
