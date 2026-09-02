# Incident 监控数据自动取证设计

## 1. 设计结论

平台在正式 Incident 创建后自动生成一次异步取证任务，并允许用户在 Incident 详情中人工重新取证。第一版接入测试环境的一套 Prometheus、Elasticsearch（ELK）和 SkyWalking，只执行版本化预定义只读查询，不允许 AI 临时生成 PromQL、Elasticsearch DSL 或 SkyWalking 查询。

Prometheus 用于证明指标发生了什么变化，Elasticsearch 用于保存有界的错误日志统计与代表性样本，SkyWalking 用于证明异常端点、依赖和 Trace。系统使用 `environment + service_name + 时间窗口` 关联三类证据，生成确定性证据摘要，不直接宣称根因。

取证不可用不得阻塞 Incident 创建、告警接收、人工确认、解决或飞书协同。

## 2. 范围与非目标

### 2.1 第一版范围

- 测试环境各配置一个启用的 Prometheus、Elasticsearch、SkyWalking 数据源；
- Incident 创建后自动取证，详情页支持人工重新取证；
- 基线窗口与故障窗口对照；
- 通用基础取证包，以及 HTTP、JVM、MySQL 专项取证包；
- Prometheus 指标趋势与对比；
- ELK 错误日志统计、错误指纹和代表性日志；
- SkyWalking 服务指标、异常端点、异常依赖和失败 Trace 摘要；
- 基于时间、服务名和 Trace ID 的确定性跨源关联；
- 持久化任务、租约、有界重试、并发收敛、审计和健康摘要；
- Incident 详情中的取证历史、关键发现、证据展开和按数据源查看。

### 2.2 第一版非目标

- AI 根因分析、RAG、自动修复或处置建议；
- AI 或用户输入直接生成监控查询；
- 用户在页面创建或修改取证包；
- 多集群、多套同类型数据源选择和跨环境查询；
- 直接访问 Kubernetes、故障注入平台、发布系统或配置中心；
- 保存完整日志文档、完整 Trace、无限时间序列或监控平台原始响应；
- 自动修改 Prometheus、Elasticsearch 或 SkyWalking；
- 跨 Incident 因果推断；
- `scenario_id`、`scenario_version`、`experiment_id`、注入动作或标准答案进入取证上下文、查询参数、证据或报告。

## 3. 业务流程

```text
Incident 创建
   ↓ 同一事务
EvidenceRun（QUEUED）+ EvidenceCollectionTask
   ↓
取证 Worker 领取持久化租约
   ↓
读取 Incident 与关联 Alert
   ↓
构建取证上下文
  environment + service_name + alertname + 可用结构化标签 + 锚点时间
   ↓
选择规则包
  common-service + HTTP/JVM/MySQL 专项包
   ↓
解析版本化只读查询模板
   ↓
Prometheus / Elasticsearch / SkyWalking 适配器
   ↓
有界标准化 EvidenceItem
   ↓
确定性跨源关联
   ↓
EvidenceRun = SUCCEEDED / PARTIAL / FAILED
   ↓
Incident 时间线追加取证摘要
```

自动取证只在首次创建 Incident 时生成一次。向已有 Incident 追加 Alert 不自动重复取证，避免告警风暴放大为监控查询风暴。用户需要新快照时人工重新取证；第一版同一 Incident 最多存在一个运行中的取证任务。

既有 Incident 不自动批量回填取证任务，可以从详情页人工发起。

## 4. 时间与查询目标

### 4.1 锚点和窗口

自动取证以 Incident 首条关联 Alert 的 `episode_started_at` 为锚点；该字段缺失时安全退化到 Alert 的 `first_received_at`，并在运行中记录退化原因。

- 基线窗口：锚点前 30 分钟至锚点前 10 分钟；
- 故障窗口：锚点前 10 分钟至本次 EvidenceRun 开始时间；
- 总查询范围硬上限：2 小时；
- 人工重新取证使用相同锚点，但以人工任务开始时间作为新的故障窗口终点。

每个 EvidenceItem 保存最终解析出的准确开始时间、结束时间、采样步长和时区。基线数据不足不伪造对比，证据项标记 `INSUFFICIENT_BASELINE`，故障期查询仍可成功。

### 4.2 查询目标

查询上下文只能来自已保存的 Incident 和关联 Alert：

- `environment`：Incident 的可信环境；
- `service_name`：Incident 分组对象或关联 Alert 中一致的 service；
- `alert_names`：关联 Alertname 去重集合；
- `entity`、`instance`、`endpoint`：仅在结构化标签已经存在时使用；
- `anchor_at`：上述锚点。

缺少 service 时不猜测服务，也不访问 Kubernetes 补充信息。依赖 service 的证据项记录 `MISSING_TARGET`，不依赖 service 的取证项继续执行。

## 5. 数据模型

### 5.1 MonitoringDataSource

监控数据源保存：

- `id`、名称、环境；
- 类型：`PROMETHEUS`、`ELASTICSEARCH`、`SKYWALKING`；
- 基础 URL、启停状态、TLS 校验开关；
- 连接超时、查询超时和安全上限；
- 非 Secret 字段映射；
- 凭据环境变量引用名与凭据完整状态；
- 乐观版本、创建和更新时间。

同一环境、同一类型最多存在一个启用数据源。第一版字段映射包括：

- Prometheus：service 与 environment 标签名；
- Elasticsearch：索引或别名、时间字段、service、environment、level、message、error type、trace ID、host 字段；
- SkyWalking：GraphQL 查询路径及 service 名称规则。

用户名、密码、API Key、Bearer Token 不进入数据库、日志、API 响应或测试数据。数据库只保存环境变量引用名；API 只返回凭据是否完整及缺失引用名。

### 5.2 EvidencePackDefinition

取证包以仓库内版本化定义存在，启动时严格校验：

- `common-service v1`：服务可用性、CPU、内存、基础错误日志与服务指标；
- `http v1`：请求量、错误率、延迟、异常端点和失败 Trace；
- `jvm v1`：堆内存、GC、线程和相关错误日志；
- `mysql v1`：连接池、数据库依赖耗时、慢调用、锁等待和连接错误日志。

每个定义包含适用 Alertname 的确定性匹配规则、证据项、数据源类型、查询模板、参数白名单、阈值、结果转换器和硬上限。`common-service` 必选，专项包根据 Alertname 和已存在的结构化标签追加；最多解析五个包。同一模板和参数组合只执行一次。

EvidenceRun 保存实际使用的包 ID、版本、模板版本和安全参数快照，使后续升级取证包后仍能解释历史结果。

### 5.3 EvidenceRun

一次取证运行保存：

- Incident ID；
- 触发方式：`AUTOMATIC` 或 `MANUAL`；
- 状态：`QUEUED`、`RUNNING`、`SUCCEEDED`、`PARTIAL`、`FAILED`；
- 锚点、基线窗口、故障窗口；
- 环境、服务和取证包版本快照；
- 成功、跳过、缺失、失败证据数量；
- 安全失败摘要；
- 发起者安全标识、创建、开始和完成时间；
- 版本和有界统计。

人工重新取证创建新 EvidenceRun，不覆盖历史运行。

### 5.4 EvidenceItem

每项查询形成不可变证据项，保存：

- EvidenceRun、证据键、中文名称和数据源类型；
- 取证包与模板版本；
- 状态：`SUCCEEDED`、`NO_DATA`、`INSUFFICIENT_BASELINE`、`MISSING_TARGET`、`SKIPPED_DEPENDENCY`、`FAILED`；
- 精确时间范围、采样步长和安全查询参数；
- 标准证据类型；
- 基线摘要、故障期摘要、变化幅度和阈值结果；
- 确定性中文含义；
- 有界标准化结果与安全失败码。

标准证据类型包括：

- `METRIC_TIMESERIES`
- `METRIC_COMPARISON`
- `LOG_AGGREGATION`
- `LOG_SAMPLE`
- `ENDPOINT_RANKING`
- `DEPENDENCY_RANKING`
- `TRACE_SUMMARY`
- `CROSS_SOURCE_CORRELATION`

### 5.5 EvidenceCollectionTask

运行任务与用户可见的 EvidenceRun 分离，保存任务状态、租约拥有者、租约到期时间、尝试次数、下一次执行时间、最后错误码和完成时间。任务使用持久化租约和指数退避；Worker 崩溃后可以重新领取。

人工操作幂等另存操作记录，只保存幂等键哈希、命令指纹、EvidenceRun ID 和安全审计字段，不保存请求正文。

## 6. 数据源适配器

### 6.1 Prometheus

使用官方稳定 HTTP API：

- 范围查询：`POST /api/v1/query_range`；
- 瞬时或聚合查询：`POST /api/v1/query`。

只允许取证包中的 PromQL 模板。模板参数经过标签值安全编码，不接受原始 PromQL API 入参。根据窗口自动计算步长，单个证据项最多保存 20 个序列、每序列 240 个降采样点；超限时安全截断并记录原因。

标准摘要包括基线与故障期平均值、最大值、中位数或 P95（由模板指定）、变化率、阈值结果和降采样趋势。

### 6.2 Elasticsearch

使用 `POST /{index_or_alias}/_search`，查询固定包含 environment、service 和时间范围过滤。`_source` 只读取配置白名单字段，返回：

- 错误日志总数和时间趋势；
- error type、exception type 或规范化 message 指纹聚合；
- 最多 50 条代表性日志；
- 可用的 Trace ID 和安全主机标识。

单条 message 最多 4,000 字符，并对常见 Token、Authorization、Cookie、密码、连接串和密钥模式脱敏。不得返回完整 `_source`、请求正文或无限分页结果。

### 6.3 SkyWalking

通过 SkyWalking Query Protocol 的 GraphQL 只读接口查询：

- 服务成功率、吞吐量和响应时间；
- 异常及慢端点排行；
- 异常下游依赖；
- 最多 20 条失败 Trace；
- 每条 Trace 最多 100 个关键 Span 的有界摘要。

证据只保存 Trace ID、入口端点、耗时、错误状态、关键 Span、失败服务和错误摘要，不保存完整 Trace 响应。

### 6.4 跨源关联

基础适配器完成后，确定性关联器使用相同 environment、service、重叠时间窗口和可用 Trace ID 形成关联证据，例如：

- 指标异常期间同一服务出现异常端点；
- 失败 Trace 在 ELK 中找到相同 Trace ID 日志；
- 错误日志增加但没有异常链路证据。

关联器只描述事实，不输出“根因”“置信度”或自动处置建议。依赖数据源失败时关联项为 `SKIPPED_DEPENDENCY`，不能把缺失证据解释为正常。

## 7. 状态、幂等与失败恢复

- Incident 创建事务同时写 EvidenceRun 和 EvidenceCollectionTask，写入失败则整个 Incident 创建事务回滚；
- 监控查询发生在后台，数据源失败不回滚 Incident；
- 自动取证唯一键保证同一 Incident 只有一条自动运行；
- 人工取证要求 Bearer Token 和 `Idempotency-Key`；精确重放返回原 EvidenceRun；
- 同一 Incident 同时只允许一个 `QUEUED` 或 `RUNNING` 运行，冲突返回当前运行；
- 单项默认超时 10 秒，整次运行硬上限 120 秒；
- 临时网络错误、429 和服务端错误有界重试，默认最多五次；
- 鉴权、权限、模板参数和永久 4xx 错误直接失败；
- 已成功 EvidenceItem 不因任务重领重复写入或覆盖；
- 有成功项也有失败、缺失或跳过项时运行是 `PARTIAL`；
- 所有可执行项失败时运行是 `FAILED`；
- 没有数据但查询成功是 `NO_DATA`，不等同于失败或正常；
- 取证完成、部分成功或失败只追加一次 Incident 活动，不改变 Incident 状态。

## 8. API

监控数据源管理：

- `GET /api/v1/monitoring-data-sources`
- `POST /api/v1/monitoring-data-sources`
- `PATCH /api/v1/monitoring-data-sources/{source_id}`
- `POST /api/v1/monitoring-data-sources/{source_id}/test`

Incident 取证：

- `GET /api/v1/incidents/{incident_id}/evidence-runs`
- `GET /api/v1/incidents/{incident_id}/evidence-runs/{run_id}`
- `POST /api/v1/incidents/{incident_id}/evidence-runs`

Incident 详情只增加最新 EvidenceRun 的状态与数量摘要，不内嵌全部证据，避免详情响应无界增长。EvidenceRun 详情按证据类型和数据源分页或有界展开。

数据源写操作、连通测试和人工取证均要求平台 Bearer Token。连通测试只返回可用性、响应时间、版本兼容状态和安全错误码，不返回响应正文、URL 中的 Secret 或请求头。

## 9. 前端产品结构

采用用户确认的第三版原型：

- 保留 Incident 中心左侧紧凑 Incident 列表；
- Incident 详情顶部保留确认和解决动作；
- 详情使用“当前情况、关联告警、监控取证、处置与飞书”分区；
- 监控取证主区首屏只显示超过阈值、形成跨源关联或明确正常的关键发现；
- 关键发现向下展开，统一展示来源、结果、对比依据和查看详情；
- 正常证据默认收起；
- 右侧集中显示三类数据源状态、锚点与窗口、取证规则包版本；
- 下方保留 Prometheus、ELK、SkyWalking 和查询依据入口；
- 支持历史 EvidenceRun 切换和人工重新取证；
- 执行中、部分成功、失败、无数据和缺少目标必须使用文字与状态共同表达，不能只依赖颜色；
- 低于 1,180px 时右侧信息栏并入主内容，页面保持可滚动和可操作。

原型路径只用于设计确认，不作为运行时代码：`.superpowers/brainstorm/49811-1788312294/content/incident-evidence-layout-v3.html`。

## 10. 数据库与模块边界

新增物理表：

- `monitoring_data_sources`
- `incident_evidence_runs`
- `incident_evidence_items`
- `evidence_collection_tasks`
- `evidence_collection_operations`

新增模块保持清晰边界：

- `MonitoringDataSourceService`：配置、唯一启用边界与连通测试；
- `EvidencePackRegistry`：加载和校验版本化定义；
- `EvidencePlanningService`：从 Incident 生成确定性执行计划；
- `PrometheusEvidenceAdapter`、`ElasticsearchEvidenceAdapter`、`SkyWalkingEvidenceAdapter`；
- `EvidenceNormalizationService`：有界结果转换与脱敏；
- `EvidenceCorrelationService`：确定性跨源关联；
- `EvidenceCollectionService` 与 `EvidenceCollectionRunner`；
- `IncidentEvidenceQueryService`：运行历史与详情读取模型。

适配器只能接收已经解析和校验的查询计划，不能接收用户原始查询文本。领域模型不依赖 HTTP 客户端或各监控平台响应结构。

## 11. 健康与可观测性

`/health` 增加有界摘要：

- 启用数据源及凭据是否完整；
- 取证任务待处理、租约中、失败数量；
- 最早待处理时间、最近成功时间和最近安全错误码；
- 三类数据源最近一次连通状态。

健康响应不返回数据源 URL、索引名、查询、Trace ID、日志内容、任务 ID、租约拥有者或 Secret。取证不可用不得让 Alert 接收和 Incident 人工运营 readiness 失败，但页面应明确显示诊断能力降级。

## 12. 测试策略

所有外部适配器使用可注入传输层与固定响应契约测试，不依赖真实监控服务完成单元和集成验证。

必须覆盖：

- Incident 创建与自动取证任务同事务；
- 自动运行唯一性、人工幂等和同 Incident 并发限制；
- 锚点退化、两个时间窗口和两小时硬上限；
- 取证包匹配、去重、版本快照和非法模板拒绝；
- 三类适配器的成功、无数据、超时、鉴权失败、限流、截断和畸形响应；
- ELK 字段白名单、日志长度限制和 Secret 脱敏；
- SkyWalking Trace/Span 上限；
- Prometheus 序列与采样点上限；
- 跨源关联与依赖缺失；
- Worker 租约重领、部分成功和完成活动幂等；
- 数据源环境隔离和每环境每类型唯一启用边界；
- API 鉴权、版本冲突、幂等重放和安全错误响应；
- 前端执行中、成功、部分成功、失败、无数据和缺少目标状态；
- 浏览器完成历史切换、证据展开和人工重新取证主流程。

真实联调最后执行：先读取测试环境 Prometheus 的实际指标和标签、ELK 字段映射及 SkyWalking 版本，再补充只适配当前测试环境的 v1 取证包。真实凭据由用户通过运行环境提供，不写入仓库。
