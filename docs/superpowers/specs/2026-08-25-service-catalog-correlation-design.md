# 服务目录与可解释事故关联设计

## 1. 状态与目标

状态：已确认，待实施。

本设计在已验收的多源 SignalEvent 与 Alert 投影之后增加轻量服务目录、持久关联任务和确定性事故关联。目标是让满足明确运营门槛的生产告警形成或加入 Incident，同时为每次创建、关联、候选提示和拒绝保存固定规则版本、事实、原因码和中文解释。

本阶段不执行自动取证，不创建 DiagnosisRun，不调用 AI，也不实现人工合并拆分、事故状态写接口或前端。告警接入成功不依赖关联处理成功；关联失败必须可重试并安全退化。

## 2. 已确认的产品规则

只有同时满足以下条件的 Alert 才能自动进入事故关联：

- 状态为 `ACTIVE`；
- 严重度为 `critical` 或 `high`；
- 环境为 `production`；
- `service + environment` 在服务目录中处于启用状态。

首版关联强度固定如下：

- 15 分钟窗口内，同环境、同服务且只有一个未结束事故候选时，自动归入该事故；
- 同服务同时存在多个候选时，不选择其中任何一个，创建独立事故并记录歧义候选；
- 没有同服务候选时，创建独立事故；
- 一跳依赖服务且标准化症状相同时，不自动合并，创建独立事故并记录“可能相关”；
- 跨环境、服务未登记、服务停用、非生产、低严重度和非 ACTIVE 告警不自动创建事故；
- Alert 恢复不会关闭 Incident，只追加“告警已恢复”的关联决策。

规则原则是宁可少合并，不可误合并。LLM、标题相似度、向量检索和黑盒评分均不得参与自动关联。

## 3. 总体结构

数据流为：

`Alert 有效变化 → 同事务关联任务 → MySQL 租约领取 → 候选过滤 → 确定性决策 → Incident/关系/解释 → 任务完成`

组件边界：

- `ServiceCatalogService`：服务目录和一跳依赖的版本化管理；
- `CorrelationEligibility`：纯领域门槛判断和固定拒绝原因；
- `CorrelationEngine`：纯领域候选决策，不访问数据库、时钟或 UUID；
- `CorrelationService`：锁定关联范围、读取候选、创建或关联 Incident、保存不可变决策；
- `CorrelationJobService`：领取、租约、重试、失败和过期接管；
- `CorrelationRunner`：核心控制器内的轻量后台循环，只调用任务服务；
- API：服务目录管理、依赖管理、关联结果读取、失败任务人工重试。

关联服务不嵌入来源适配器。Alertmanager 和 CloudEvents 继续只输出 SignalCommand，共享接入服务在 Alert 投影真正变化时追加关联任务。

## 4. 持久化模型

新增迁移 `0002_service_catalog_correlation`，不得修改已经应用的 `0001_mysql_initial`。

### 4.1 `service_catalog_entries`

- `id`：`svc_<32 hex>`；
- `service`：1–128 字符；
- `environment`：现有四类环境枚举；
- `owner_team`：1–128 字符，只保存团队标识，不保存电话、Token 或任意联系人负载；
- `state`：`ACTIVE | INACTIVE`；
- `created_at`、`updated_at`：UTC `DATETIME(6)`；
- `version`：从 1 开始的乐观锁版本。

`service + environment` 唯一。停用采用状态更新，不提供物理删除。

### 4.2 `service_catalog_state`

该表只有 `id = global` 一行，保存 `graph_version` 和 `updated_at`。所有依赖新增或启停事务必须先锁定该行，完成环检测后递增版本。它不保存服务正文，只作为依赖图并发串行化边界。

### 4.3 `service_dependencies`

- `id`：`dep_<32 hex>`；
- `caller_service_id`、`dependency_service_id`：服务目录外键；
- `state`：`ACTIVE | INACTIVE`；
- `created_at`、`updated_at`、`version`。

同一有向边唯一。禁止自依赖、跨环境依赖和任何有向依赖环。环检测由服务层在事务中完成；数据库负责外键、唯一和自依赖检查约束。依赖写事务先锁定一个单例 `service_catalog_state` 图版本行，校验完整 ACTIVE 图后再写边并递增图版本，避免并发写入分别通过校验后共同形成环。首版关联只读取一跳，不执行传递闭包。

### 4.4 `correlation_jobs`

- `id`：`cjob_<32 hex>`；
- `alert_id`、`alert_version`：关联的 Alert 快照身份；
- `state`：`PENDING | LEASED | SUCCEEDED | FAILED`；
- `attempts`：0–5；
- `available_at`：下次允许领取时间；
- `lease_owner`：有界运行实例标识；
- `lease_expires_at`：租约过期时间；
- `last_error_code`：固定错误码，不保存异常正文；
- `created_at`、`updated_at`。

`alert_id + alert_version` 唯一。opened、updated、resolved、reopened 才生成任务；stale、orphan_resolved 和幂等重放不生成任务。任务和 Alert 投影变化在同一事务提交。旧版本任务被领取时如果 Alert 已有更高版本，则以 `alert_version_superseded` 完成，不使用当前内容冒充旧版本决策。

### 4.5 `incident_alert_links`

- `incident_id`、`alert_id`：联合主键；
- `relation`：`PRIMARY | RELATED`；
- `decision_id`：建立关系的关联决策；历史人工报告回填关系允许为空；
- `linked_at`、`created_at`。

一个 Alert 首版最多归属一个 Incident，通过 `alert_id` 唯一约束保证。迁移把已有 Incident 的 `primary_alert_id` 回填为 `PRIMARY` 关系，不改变现有人工报告数据。

### 4.6 `correlation_decisions`

- `id`：`cdec_<32 hex>`；
- `job_id`：每个完成任务最多一个不可变决策；
- `alert_id`、`alert_version`；
- `incident_id`：可空；
- `outcome`：固定结果枚举；
- `rule_version`：首版固定 `correlation.v1`；
- `reason_codes`：最多 10 个固定原因码；
- `facts`：有界 JSON，只允许环境、服务、严重度、标准症状、窗口秒数、候选数量和候选 Incident ID；
- `candidate_incident_ids`：最多 20 个；
- `explanation`：由固定模板生成的中文解释，最多 500 字符；
- `created_at`。

固定结果包括：`CREATED_NO_MATCH`、`LINKED_EXACT_SERVICE`、`LINKED_EXISTING`、`CREATED_AMBIGUOUS`、`CREATED_DEPENDENCY_CANDIDATE`、`REJECTED_INELIGIBLE`、`RECORDED_RESOLUTION`、`SUPERSEDED`。

决策不保存 Alert 标题、摘要、原始来源 URI、查询、完整标签、异常正文或 Secret。

## 5. 标准症状

一跳候选只使用受控症状，不从标题自由推断。首版固定症状为：

- `error_rate`；
- `latency`；
- `cpu_saturation`；
- `memory_pressure`；
- `availability`。

来源事实中的 `symptom` 使用固定别名表转换；未知或缺失得到 `None`，此时不得触发一跳相关候选。Alertmanager 的事实白名单增加 `symptom`；CloudEvents 与人工报告继续通过有界 labels 提供该值。原始未知值不进入关联决策。

## 6. 候选和关联算法

关联窗口固定为 15 分钟，首版不提供运行时修改。窗口使用 Alert 的 `last_observed_at` 与 Incident 的 `detected_at` 比较，绝对时间差不超过 900 秒时包含边界。候选 Incident 只包括 `DETECTED`、`TRIAGING`、`INVESTIGATING`、`MITIGATING` 和 `MONITORING_RECOVERY`，排除 `RESOLVED` 与 `CLOSED`。

处理顺序：

1. 校验任务 Alert 版本；版本已过期则保存 `SUPERSEDED`；
2. Alert 已有 Incident 关系且仍 ACTIVE 时保存继续归属决策，不重复建联；
3. Alert 已有关系且已 RESOLVED 时保存 `RECORDED_RESOLUTION`，不改变 Incident；
4. 执行严重度、环境、状态和目录门槛；不符合时保存 `REJECTED_INELIGIBLE`；
5. 对服务目录行执行 `SELECT ... FOR UPDATE`，串行化同服务并发关联；
6. 查询 15 分钟内同环境、同服务的未结束 Incident；
7. 恰好一个候选时创建 `RELATED` 关系并保存 `LINKED_EXACT_SERVICE`；
8. 多个候选时创建新 Incident 与 `PRIMARY` 关系，保存 `CREATED_AMBIGUOUS` 和有界候选 ID；
9. 没有同服务候选时查询一跳服务的同症状候选；无论是否存在一跳候选都创建独立 Incident；有候选保存 `CREATED_DEPENDENCY_CANDIDATE`，无候选保存 `CREATED_NO_MATCH`。

新 Incident 从 Alert 的规范化字段复制标题、严重度、服务和环境，状态固定为 `DETECTED`，`detected_at` 使用 Alert 最近有效事件时间。关联新 Alert 时保留 primary Alert 和标题；如果新 Alert 严重度更高，只提升 Incident 严重度并递增版本，不自动降低严重度。外部 Alert 创建 Incident 时不得创建 DiagnosisRun。

一跳关系在候选提示中按无向邻接处理：调用方和被依赖方均视为一跳，但永远不能据此自动合并。一跳症状候选通过 Incident 已关联 Alert 的当前 SignalEvent 白名单事实判断；任一关联 Alert 的标准症状匹配即可成为候选。候选查询最多返回 20 条，超过上限时按歧义处理并保存固定 `candidate_limit_reached` 原因，不截断后自动合并。

首版原因码和中文模板固定为：

- `alert_not_active`：告警当前不是活动状态，未进入事故关联；
- `severity_below_threshold`：告警严重度未达到 critical/high，未创建事故；
- `non_production_environment`：告警不属于生产环境，未创建事故；
- `service_not_registered`：服务尚未登记，未创建事故；
- `service_inactive`：服务目录项已停用，未创建事故；
- `alert_version_superseded`：该任务对应的告警版本已被新版本取代；
- `alert_already_linked`：告警已经属于现有事故，保持原关联；
- `alert_resolved_no_incident_close`：告警已经恢复，但事故不会自动关闭；
- `no_same_service_candidate`：窗口内没有同服务事故，已创建独立事故；
- `one_exact_service_candidate`：窗口内只有一个同服务事故，已自动关联；
- `multiple_exact_service_candidates`：窗口内存在多个同服务事故，为避免误合并已创建独立事故；
- `dependency_symptom_candidate`：发现一跳服务的同症状事故，仅标记可能相关；
- `candidate_limit_reached`：候选数量超过安全上限，未执行自动合并。

API 返回的中文解释只能由这些模板和有界计数、资源 ID 组合，不接受外部自由文本模板。

同服务并发任务通过目录行锁串行处理，关系唯一约束防止一个 Alert 进入多个 Incident。唯一约束竞争必须在新事务中有界重试，不能在失败事务中继续查询。

## 7. 持久任务与后台运行

核心控制器启动一个轻量 `CorrelationRunner`，不拆独立 Worker。领取使用 MySQL 8.4 的行锁和 `SKIP LOCKED`：

- 每次最多领取固定小批次；
- 租约和轮询周期由有界 Settings 提供；
- 进程退出后，租约过期任务可被其他实例接管；
- 失败使用固定退避并最多尝试 5 次；
- 最终失败只保存固定 `last_error_code`；
- 人工重试只能把 FAILED 任务恢复为 PENDING，并产生审计；
- 后台循环异常不得终止 FastAPI 进程或影响存活、就绪和接入接口。

测试直接调用任务与关联服务，不依赖后台等待。后台生命周期只做启动、停止和异常隔离测试。

## 8. 服务目录与关联 API

管理和读取接口使用现有 `II_API_TOKEN`，不接受 Alertmanager 或 CloudEvents Token。

服务目录接口：

- `POST /api/v1/catalog/services`：创建，首次返回 201；
- `GET /api/v1/catalog/services`：有界分页和固定筛选；
- `GET /api/v1/catalog/services/{id}`：读取；
- `PATCH /api/v1/catalog/services/{id}`：携带 `expected_version` 修改负责人或启停状态；
- `POST /api/v1/catalog/dependencies`：创建一跳依赖；
- `GET /api/v1/catalog/dependencies`：按服务和状态筛选；
- `PATCH /api/v1/catalog/dependencies/{id}`：携带 `expected_version` 启停依赖。

关联接口：

- `GET /api/v1/alerts/{id}/correlation`：返回任务状态、Incident 关系、最近决策、固定原因和中文解释；
- `GET /api/v1/correlation/jobs`：按状态有界查询；
- `POST /api/v1/correlation/jobs/{id}/retry`：只允许重试 FAILED 任务。

所有写接口使用乐观锁和有界审计。不存在与无权访问继续使用安全固定错误。首版目录修改只影响后续 Alert 变化和失败任务重试，不自动全库重跑历史 Alert；批量重算留待独立规格。

## 9. 错误、安全与恢复

新增固定错误包括：

- `catalog_conflict`：同环境服务或依赖边已存在；
- `catalog_version_conflict`：乐观锁版本不一致；
- `invalid_dependency`：自依赖、跨环境或依赖环；
- `correlation_job_not_retryable`：任务不是 FAILED；
- `correlation_unavailable`：关联处理失败，但不用于外部信号接入响应。

目录和关联审计只保存资源 ID、动作、固定原因码、前后状态和版本，不保存请求正文、负责人联系方式、Alert 标题摘要、候选原始负载或异常堆栈。禁止身份递归检查继续应用于管理写入和关联事实。

数据库不可用时目录写入返回安全 503。关联处理失败回滚本次 Incident、关系和决策写入，再有界更新任务重试状态。外部接入已经提交的 SignalEvent 和 Alert 不受影响。

## 10. 验收策略

自动化测试至少覆盖：

- `0001 → 0002 → 0001` 迁移往返、ORM 一致性和已有 Incident 关系回填；
- 服务创建、唯一性、乐观锁、停用、认证、分页和安全错误；
- 依赖创建、自依赖、跨环境、重复边、依赖环和停用；
- 固定症状别名与未知值安全退化；
- 门槛的 ACTIVE、严重度、production 和目录启用四个条件；
- 同服务零候选、唯一候选、多候选；
- 一跳同症状候选与未知症状不候选；
- 跨环境、终态 Incident 和窗口外 Incident 不关联；
- Alert 恢复只记录决策，不关闭 Incident；
- 任务唯一、重放不重复、旧版本跳过、租约、过期接管、重试上限和人工重试；
- 同服务并发创建收敛、Alert 关系唯一和事务失败零残留；
- 外部 Alert 创建 Incident 后 DiagnosisRun 保持为零；
- 每个自动结果都有规则版本、固定原因码、有界事实和中文解释；
- API、日志、审计和数据库不出现 Secret、原始来源 URI、查询、实验身份、注入动作或标准答案；
- 统一 Ruff、格式、Mypy、MySQL 集成测试和覆盖率 90% 门槛。

## 11. 非目标与后续边界

本阶段不包含：

- 自动取证、证据快照、Diagnosis Worker、Analysis Worker 或 AI；
- 告警标题语义相似度、向量数据库、LLM 关联或机器学习；
- 人工合并、拆分、移动 Alert 或事故运营状态写接口；
- 维护窗口、部署变更、Feature Flag 和完整服务目录产品；
- 多跳依赖、图数据库和自动拓扑发现；
- 目录变化后的历史 Alert 批量重算；
- 前端页面和生产部署制品。

完成本设计后，主数据流只推进到“外部 Alert → 可解释 Incident”。诊断、AI 和运营闭环必须继续由后续规格实施。
