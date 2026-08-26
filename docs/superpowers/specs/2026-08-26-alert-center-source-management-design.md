# 告警中心与告警源管理设计

## 1. 背景

平台已经能够通过 Alertmanager Webhook v4、CloudEvents 1.0 和人工报告接收信号，维护 Alert 投影并执行可解释事故关联。现有事故中心也已经具备真实数据读取和人工处置闭环。

当前仍有两个明显缺口：

1. 前端只有事故中心，无法独立查看全部告警、归并过程和事故关联结果；
2. Alertmanager 与 CloudEvents 依赖全局环境 Token 和固定入口，无法按接入系统独立配置、停用、轮换凭据和观察接收状态。

本阶段建设告警中心和告警源管理，使操作员能够回答：告警从哪里来、实际检测到什么、平台如何处理、是否进入事故，以及接入系统最近是否真正发送过有效数据。

## 2. 目标

- 为每个外部告警发送方建立独立、可停用、可审计的告警源身份；
- 为每个告警源生成独立 Webhook 地址和只展示一次的高熵 Token；
- 保留现有适配器、SignalEvent、Alert 投影和事故关联实现；
- 增加有界告警列表、统计和聚合详情 API；
- 增加真实后端驱动的告警中心与告警源管理页面；
- 让空数据、接入失败、关联失败和后端不可用具有明确且不误导的中文状态。

## 3. 非目标

- 不新增 Prometheus、日志、APM、Kubernetes 或云厂商适配器；
- 不实现告警确认、人工关闭、抑制、静默或通知升级；
- 不让告警状态替代事故处置状态；
- 不实现生产级 RBAC、SSO 或外部密钥管理服务；
- 不保存原始 Webhook 请求、请求头、明文 Token 或外部敏感 URL；
- 不引入 AI、自动取证、故障场景、实验身份或实验专用告警；
- 不通过核心控制器主动访问监控系统判断连接状态。

## 4. 采用方案

采用“来源注册表包裹现有适配器”的渐进式方案。

新建告警源注册表和独立凭据模型。请求先根据 URL 中的 `alert_source_id` 定位来源，再校验该来源的凭据和启用状态，最后调用现有 Alertmanager 或 CloudEvents 纯适配器。适配器输出仍是统一 `SignalCommand`，后续 SignalEvent 持久化、Alert 投影、关联任务和事故创建保持原有边界。

现有固定入口继续作为兼容入口，分别映射到系统管理的 Alertmanager 和 CloudEvents 兼容来源。人工报告映射到系统管理的人工来源。新建来源只使用独立入口。兼容入口在本阶段不删除，避免已有接入立即失效。

未采用以下方案：

- 为每种来源复制一套完整接入服务：会重复幂等、事务和安全逻辑；
- 立即删除全局 Token 和固定入口：迁移风险过高，会破坏当前已验证链路；
- 控制器主动探测监控系统：无法证明告警推送链路真实可达，并会扩大网络权限。

## 5. 领域与持久化设计

### 5.1 AlertSource

`alert_sources` 保存：

- `id`：稳定内部标识；
- `name`：同一安装内唯一的操作员名称；
- `source_type`：`ALERTMANAGER`、`CLOUDEVENTS` 或系统来源 `MANUAL`；
- `management_type`：`USER_MANAGED` 或 `SYSTEM_MANAGED`；
- `state`：`ENABLED` 或 `DISABLED`；
- `version`：管理写操作的乐观版本；
- `last_accepted_at`、`last_rejected_at`、`last_validated_at`；
- 有界累计计数：成功请求、拒绝请求、创建、更新、恢复、重复和忽略数量；
- 创建和更新时间。

已有数据迁移到三个确定性系统来源：人工报告、Alertmanager 兼容接入和 CloudEvents 兼容接入。系统来源不可删除、不可改类型；兼容来源保留现有环境 Token 行为。所有 Alert 和 SignalEvent 最终都具有不可变且非空的 `alert_source_id`。

告警源一旦被 Alert 或 SignalEvent 引用便永久保留身份，只允许停用，不提供硬删除接口。

### 5.2 AlertSourceCredential

`alert_source_credentials` 保存：

- 公开的凭据 ID；
- 所属告警源；
- Token 的不可逆 SHA-256 摘要；
- 状态 `ACTIVE` 或 `REVOKED`；
- 创建、最后使用和撤销时间；
- 创建与撤销主体的安全摘要。

Token 使用至少 256 位密码学安全随机数。返回形态包含公开凭据 ID，使服务端能够先定位记录，再恒定时间比较摘要。数据库、日志、审计、API 后续读取和前端状态中都不保存明文 Token。

创建来源和轮换凭据时，明文 Token 只在首次成功 HTTP 响应中展示。幂等操作记录只保存来源 ID、凭据 ID 和结果码，不保存明文。若首次响应丢失，相同幂等键重放只返回 `secret_retrievable=false`，操作员必须显式轮换；前端不得自动重试生成 Token 的写请求。

启用的用户来源至少保留一个有效凭据。撤销最后一个有效凭据前必须先停用来源或先完成轮换。轮换默认保留旧凭据，便于发送端平滑切换；旧凭据由操作员显式撤销。

### 5.3 AlertSourceReceipt

`alert_source_receipts` 保存已认证请求的安全处理摘要：

- 告警源、适配器类型和接收时间；
- 固定结果码：接受、精确重放、验证成功、格式拒绝、来源停用或处理失败；
- 输入条数以及创建、更新、恢复、重复和忽略计数；
- 固定安全原因和请求关联 ID。

Receipt 不保存请求正文、请求头、Token、原始 URL、异常堆栈、告警标题或摘要。每个来源物理记录最多保留最近 1,000 条；写入新记录时删除该来源超过 30 天的记录和超出数量上限的旧记录。读取接口始终排除超过 30 天的记录，因此即使来源长期没有新请求，对外窗口也保持有界。

未通过认证的请求不逐条写数据库，避免攻击者利用认证失败制造无界数据；只进入有界运行指标和脱敏日志。数据库不可用时返回安全错误，不能虚构 Receipt 或成功计数。

### 5.4 来源隔离

`SignalCommand` 增加可信 `alert_source_id`，由接入路由在认证后注入，不从外部负载读取。`SignalEvent`、`Alert`、接入结果和关联任务保留该身份。

事件幂等身份和 Alert 投影唯一身份都加入 `alert_source_id`。两个注册来源即使发送完全相同的 Alertmanager externalURL、CloudEvents source/id 或 alert key，也不会相互冲突或归并。

现有 `source`、`source_instance` 和 `source_alert_key` 继续保留，用于适配器类型和外部稳定身份摘要；它们不能替代可信来源身份。

## 6. 接入接口与数据流

### 6.1 独立入口

- `POST /api/v1/intake/alertmanager/{alert_source_id}`；
- `POST /api/v1/intake/cloudevents/{alert_source_id}`；
- `POST /api/v1/intake/alertmanager/{alert_source_id}/validate`；
- `POST /api/v1/intake/cloudevents/{alert_source_id}/validate`。

普通入口执行真实接入。验证入口使用同一认证、容量和 Schema 校验，但只更新来源验证状态并写安全 Receipt，不创建 SignalEvent、Alert、关联任务或 Incident。验证请求必须从实际发送方网络发出，控制器不主动回调外部系统。

固定兼容入口继续存在：

- `POST /api/v1/intake/alertmanager`；
- `POST /api/v1/intake/cloudevents`。

它们分别绑定系统管理的兼容来源和现有环境 Token。

### 6.2 真实接收顺序

1. 从路由参数读取来源 ID；
2. 解析 Token 中的公开凭据 ID；
3. 查询来源和凭据，执行恒定时间摘要比较；
4. 拒绝类型不匹配、已停用来源或已撤销凭据；
5. 执行路径级请求体容量限制和适配器 Schema 校验；
6. 注入可信 `alert_source_id` 并生成统一 `SignalCommand`；
7. 在一个 MySQL 事务中写入 Receipt、SignalEvent、Alert 投影、接入结果、审计和必要的关联任务；
8. 精确重放不新增领域记录，Receipt 明确标记重复；
9. 成功后更新来源时间和累计计数。

适配器校验失败发生在领域事务前。认证已经通过时，平台使用独立小事务记录固定失败 Receipt；业务数据保持零写入。领域事务失败时完整回滚，再尽力记录不含输入的处理失败 Receipt；Receipt 记录失败不能改变原错误结果。

## 7. 告警源管理接口

全部管理写接口使用现有人工控制器认证、服务端认证主体和 `Idempotency-Key`。修改、轮换和撤销还必须提交 `expected_version`；创建来源没有既有版本，不提交该字段。

- `POST /api/v1/alert-sources`：创建用户管理来源并返回一次性 Token；
- `GET /api/v1/alert-sources`：按类型、启用状态和接收状态分页查询；
- `GET /api/v1/alert-sources/{id}`：读取来源、安全凭据元数据和统计；
- `PATCH /api/v1/alert-sources/{id}`：修改名称或启用状态；
- `POST /api/v1/alert-sources/{id}/credentials/rotate`：创建新凭据并一次性返回 Token；
- `POST /api/v1/alert-sources/{id}/credentials/{credential_id}/revoke`：撤销旧凭据；
- `GET /api/v1/alert-sources/{id}/receipts`：读取最近接收记录。

系统来源只允许读取。来源类型创建后不可修改。管理接口不提供 Token 找回和来源硬删除。

接收状态由已持久化事实派生，不进行主动探测：

- `WAITING_FIRST_DATA`：从未验证或接收；
- `RECEIVED`：最近一次事实为验证成功或真实接收成功，同时展示时间；
- `REJECTED`：最近一次已认证事实为拒绝，同时展示安全原因；
- `DISABLED`：来源已停用。

页面中文分别显示“等待首次数据”“已收到数据”“最近接收失败”和“已停用”，不得显示“连接正常”等无法证明的结论。

## 8. 告警读取接口

### 8.1 告警列表

`GET /api/v1/alerts` 返回 `items`、`limit`、`offset` 和 `total`，单页最多 100 条，默认按最近观测时间倒序。支持：

- `state`；
- `severity`；
- `alert_source_id`；
- `source_type`；
- `service`；
- `environment`；
- `from` 和 `to`；
- 标题、服务和环境的有界 `query`；
- `incident_linked`。

列表只返回页面必需的白名单字段，不加载原始事件正文或审计详情。

### 8.2 告警统计

`GET /api/v1/alerts/summary?window=24h` 返回当前告警、严重告警、窗口内已恢复和未关联事故数量，以及按告警源的有界分组。窗口只接受后端定义的固定值，首版为 `1h`、`24h` 和 `7d`。

### 8.3 告警详情

`GET /api/v1/alerts/{alert_id}/overview` 聚合：

- Alert 当前投影；
- 告警源名称、类型和安全接收状态；
- 最多 100 条关联 SignalEvent 的规范化事实和投影结果；
- 最新关联决策、固定原因码和中文解释；
- 已关联 Incident 的 ID、标题和运营状态；
- 数据截断标志。

现有 `GET /api/v1/alerts/{id}` 和 `GET /api/v1/alerts/{id}/correlation` 保持兼容。Overview 只组合白名单投影，不返回幂等指纹、来源凭据、原始 payload、内部任务租约或实验字段。

## 9. 前端信息架构

### 9.1 告警中心

现有左侧导航中的“告警”变为真实页面。桌面端采用列表和详情双栏：

- 顶部展示当前告警、严重告警、未关联事故和 24 小时已恢复；
- 列表支持状态、严重程度、来源、服务、环境、时间和关键字筛选；
- 详情首先展示“实际检测结果”，再展示来源、时间、归并信号和事故关联；
- “系统处理过程”使用中文步骤说明接入、归并和关联结果；
- “为什么这样处理”展示后端固定中文解释；
- 点击已关联事故可进入事故中心对应详情。

告警中心是只读运营视图。本阶段不增加告警确认、关闭或抑制按钮。

### 9.2 告警源管理

“系统设置”下增加“告警源管理”：

- 列表展示名称、类型、启用状态、接收状态、最近收到时间和 24 小时计数；
- 详情展示专属 Webhook、凭据元数据和最近 Receipt；
- 创建流程在确认页一次性展示 Token，并要求操作员显式确认已保存；
- 轮换流程先生成新 Token，旧 Token 保持有效，随后可单独撤销；
- 停用操作明确说明历史告警保留、新请求将被拒绝。

系统来源显示“系统管理”，不展示修改、轮换和撤销操作。

### 9.3 页面状态

- 加载中、真实空数据、读取失败和部分数据缺失必须独立展示；
- API 不可用时保留已读取详情，不切换到演示数据；
- 告警归并或关联失败时告警仍可查看，并显示“等待重新处理”；
- 来源没有成功数据时显示“等待首次数据”，不能显示为健康；
- 内部枚举、UUID、异常堆栈和数据库术语默认不进入业务页面；
- 移动宽度下列表和详情纵向排列，关键操作仍可键盘访问。

## 10. 错误与恢复契约

新增稳定错误码：

- `alert_source_not_found`；
- `alert_source_name_conflict`；
- `alert_source_version_conflict`；
- `alert_source_disabled`；
- `alert_source_type_mismatch`；
- `credential_not_found`；
- `credential_already_revoked`；
- `last_active_credential`；
- `authentication_required`；
- `invalid_source_payload`；
- `persistence_unavailable`。

错误消息使用中文且不回显来源 ID 之外的被拒绝值、Token、请求体或堆栈。来源注册表或关联引擎异常不能把已认证输入标记为成功。关联失败不回滚已经成功接收的告警，由现有持久任务安全重试或退化为独立事故。

读取接口失败不改变任何领域状态。告警源停用后返回稳定拒绝；重新启用后原有效且未撤销的凭据恢复可用。

## 11. 安全边界

- 所有外部负载继续执行禁止实验身份递归扫描；
- `scenario_id`、`scenario_version`、`experiment_id`、注入动作和标准答案不得进入任何新模型、Receipt、API 或页面；
- Token 不进入源码、迁移、fixture、日志、错误响应、前端构建或审计；
- 管理写操作使用现有人工认证主体，不信任客户端提交的 actor；
- 动态来源入口继续执行 64 KiB CloudEvents、256 KiB Alertmanager 和单批 100 条边界；
- 所有列表和明细具有固定分页、数量和字段容量；
- 来源类型、来源 ID 和凭据归属必须同时匹配，防止跨来源或跨适配器使用；
- 所有安全审计只保存固定动作、资源 ID、主体摘要和固定结果，不保存业务正文。

## 12. 测试与验收

### 12.1 后端

- 迁移升级、降级、回填、外键、唯一约束和凭据状态约束；
- Token 创建只展示一次、摘要认证、恒定时间比较、轮换、撤销和最后凭据保护；
- 两个来源发送相同外部身份仍完全隔离；
- 兼容入口行为和现有全局 Token 保持不变；
- 验证入口不创建 SignalEvent、Alert、关联任务或 Incident；
- 正常接入、更新、恢复、精确重放、格式错误、类型不匹配、停用来源和数据库失败；
- Receipt 内容白名单、1,000 条和 30 天边界；
- 告警列表筛选、排序、分页、总数、统计窗口和详情截断；
- 关联失败时告警仍可读取，且不产生虚假关联结论；
- 禁止身份、Token 形态和敏感字段扫描零泄漏。

### 12.2 前端

- 告警导航、列表筛选、选择详情和事故跳转；
- 告警统计、归并步骤、关联解释和截断提示；
- 告警源创建、一次性 Token 确认、轮换、撤销、停用和重新启用；
- 等待首次数据、已收到、最近失败、停用、空、加载、失败和安全重试状态；
- API 失败不展示演示数据，不丢弃当前已读取事实；
- 生产构建不包含 Token、内部认证主体或禁止实验字段。

### 12.3 真实链路

使用两个独立来源分别发送相同 Alertmanager 或 CloudEvents 身份，验证形成隔离 Alert；重复发送只更新 Receipt，不制造重复领域对象；一条满足门槛的告警经现有关联 Runner 进入 Incident。浏览器验证告警中心、告警详情、来源状态、接收记录和事故跳转均来自 MySQL 真实数据。

## 13. 实施拆分

本设计按可独立验收的顺序拆为：

1. 来源注册表、系统来源回填与来源身份迁移；
2. 独立凭据生命周期与安全认证；
3. 动态来源入口、验证入口和有界 Receipt；
4. 告警列表、统计和聚合详情 API；
5. 告警中心前端；
6. 告警源管理前端；
7. 兼容回归、真实 MySQL/浏览器验收和状态文档更新。

每一阶段先写失败测试，再实现最小完整行为，并在 `main` 分支顺序提交。实现不使用子 Agent 或 Git worktree。
