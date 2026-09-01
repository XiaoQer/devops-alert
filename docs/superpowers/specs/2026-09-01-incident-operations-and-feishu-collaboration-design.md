# Incident 自动生成、运营与飞书协同设计

## 1. 设计结论

已发布 Incident 规则命中新到达的 Alert 后，平台直接创建正式 Incident，不设置候选或人工批准阶段。Alert 接收、规则评估和飞书通知彼此解耦：接收事务只保存 Alert 事实和持久化评估任务，后台处理器负责创建或更新 Incident，再通过通知任务发送飞书消息。

用户处理 Incident，系统保存 Alert。Incident 是通知与处置单位，Alert 是 Incident 的证据明细，二者不得退化为一对一复制。

## 2. 业务流程

```text
Alertmanager / CloudEvents
          ↓
保存 SignalEvent 与 Alert
          ├─ 同一事务写入 IncidentEvaluationJob
          └─ 立即返回接收结果
                     ↓
             异步规则评估器
                     ↓
        已发布规则是否命中当前窗口
          ├─ 否：完成任务并记录原因
          └─ 是：锁定去重边界
                     ↓
        创建 Incident 或追加关联 Alert
                     ↓
             写入通知 Outbox
                     ↓
         飞书应用机器人发送/更新卡片
                     ↓
   群成员在线程中 @机器人 → Incident 时间线
```

## 3. Incident 身份与去重

进行中 Incident 的唯一业务边界为：

```text
(incident_rule_id, environment, group_key, unresolved)
```

- `group_key` 来自规则的 `group_by`：同一服务使用 service，同一实体使用 Alert 已有 entity key；
- `OPEN` 和 `ACKNOWLEDGED` 都属于 unresolved；
- 相同边界存在 unresolved Incident 时，新命中只追加 Alert 并更新统计；
- 原 Incident 进入 `RESOLVED` 后，相同边界再次命中会创建新 Incident；
- 第一版不跨规则合并。两个不同规则同时命中可能产生两个 Incident，页面必须显示来源规则，后续再独立设计跨规则关联。

并发控制使用数据库唯一边界、行锁和幂等任务三层保证，不能只依赖进程内锁。

## 4. Incident 数据模型

### 4.1 Incident

核心字段：

- `id`：内部稳定 ID；
- `reference`：面向用户的编号，例如 `INC-20260901-001`；
- `title`：第一版由后端确定性生成“{环境} {聚合对象}异常”；
- `state`：`OPEN`、`ACKNOWLEDGED`、`RESOLVED`；
- `severity`：`critical/high/medium/low`，取关联 Alert 的最高级别；
- `environment`、`group_by`、`group_key`、`group_display_name`；
- `incident_rule_id` 与创建时的 `incident_rule_version`；
- `alert_count`、`active_alert_count`、`distinct_alert_name_count`；
- `opened_at`、`acknowledged_at`、`resolved_at`、`last_alert_at`；
- `version`、创建和更新时间。

严重级别只允许自动升级，不因 Alert 恢复自动降级。用户首版不手工修改标题和严重级别，避免页面状态与规则事实出现两套含义。

### 4.2 IncidentAlert

Incident 与 Alert 使用独立关联表，`(incident_id, alert_id)` 唯一。关联表保存首次关联时间、关联规则版本和是否属于首次触发窗口，不复制 Alert 全文。

同一 Alert 可以被不同规则产生的 Incident 关联；第一版不尝试全局唯一归属。

### 4.3 IncidentActivity

不可变时间线，类型包括：

- `INCIDENT_CREATED`
- `ALERTS_LINKED`
- `SEVERITY_ESCALATED`
- `ALL_ALERTS_RECOVERED`
- `ACKNOWLEDGED`
- `RESOLVED`
- `FEISHU_MESSAGE_RECORDED`
- `NOTIFICATION_FAILED`

每条活动保存发生时间、操作者类型与安全标识、中文摘要和有界元数据。飞书消息只保存规范化纯文本、发送人 open_id 的不可逆摘要或安全展示名、chat_id/message_id 以及来源时间，不保存完整事件请求。

## 5. 状态机

```text
OPEN ──确认──> ACKNOWLEDGED ──解决──> RESOLVED
  └────────────────解决─────────────────>
```

- `OPEN` 可以确认或直接解决；
- `ACKNOWLEDGED` 只能解决；
- `RESOLVED` 是终态，不自动重开；
- 确认和解决都要求期望版本与幂等键；
- 解决要求填写 1–2,000 字解决说明；
- Alert 全部恢复只追加 `ALL_ALERTS_RECOVERED`，不改变状态；
- 已解决后新告警命中会产生新的 Incident。

## 6. 实时规则评估

历史试运行与实时评估必须共用同一个纯领域评估器，避免“试运行能命中、生产不命中”。实时任务以发生变化的 Alert 为锚点：

1. 读取 Alert 接收时的可信环境快照；
2. 查询匹配环境、来源和服务过滤条件的已发布规则；
3. 按每条规则的 `window_minutes` 有界读取同一分组窗口；
4. 执行既有四类 AND 条件；
5. 命中后得到完整成员 Alert ID 集合；
6. 锁定 unresolved 去重边界并创建或更新 Incident；
7. 只对新成员写关联和统计，重复任务不产生新变化。

每个评估任务记录 Alert ID、Alert version、原因、尝试次数、下一次执行时间和最终状态。单任务有规则数、查询条数和运行时间上限；失败按退避策略重试，超过上限进入失败状态并在健康信息中可见。

规则停用只阻止新评估，不自动解决已经存在的 Incident。

## 7. 飞书双向协同

### 7.1 集成方式

使用飞书企业自建应用并启用机器人，不采用只能单向推送的群自定义 Webhook。应用凭据通过运行环境提供：

- `II_FEISHU_APP_ID`
- `II_FEISHU_APP_SECRET`
- `II_FEISHU_VERIFICATION_TOKEN`
- `II_FEISHU_ENCRYPT_KEY`（启用事件加密时）

数据库只保存非 Secret 配置和状态。

### 7.2 通知路由

每个环境最多配置一个启用路由：环境、飞书 `chat_id`、展示名称、启停状态和版本。Incident 页面提供“飞书通知配置”入口；App Secret 等凭据不在页面输入。

没有启用路由时 Incident 仍正常创建，页面显示“未配置飞书群”，通知任务不无限重试。

### 7.3 消息卡片与线程

Incident 创建后向对应固定事故群发送一张卡片，保存 `chat_id + root_message_id` 作为线程绑定。卡片展示编号、标题、环境、对象、严重级别、触发依据、Alert 数量和当前状态，提供：

- 查看 Incident；
- 确认事故；
- 解决事故。

Alert 追加只更新卡片统计，不逐条向群发送消息；严重级别升级、全部 Alert 恢复、确认和解决在原线程追加一条简短状态消息。这样用户收到的是 Incident 变化，而不是 Alert 风暴。

### 7.4 群聊回流

只处理同时满足以下条件的消息：

- 来自已启用环境路由的 `chat_id`；
- 是某个 Incident 根消息的回复线程；
- 明确 `@` 当前机器人；
- 发送者不是机器人自身；
- 事件 ID 尚未处理。

普通文字规范化为最多 4,000 字纯文本，追加 `FEISHU_MESSAGE_RECORDED`。机器人在线程回复“已记录到 {Incident 编号}；聊天内容不会自动改变事故状态”。第一版忽略附件、图片、编辑、撤回和不在线程内的消息。

卡片按钮回调是明确状态操作：确认直接执行；解决先要求填写解决说明，再执行解决。回调校验、版本控制和幂等规则与页面 API 相同。

### 7.5 通知可靠性

通知使用持久化 Outbox。唯一通知键按 Incident、活动类型和活动 ID 生成。发送成功记录飞书消息 ID；网络错误、限流和服务端错误按有界指数退避重试；永久鉴权或权限错误进入失败状态并在 Incident 详情及通知配置中展示。

飞书不可用永远不会回滚 Incident 或阻断 Alert 接收。

## 8. 后端组件与数据表

新增独立模块：

- `IncidentEvaluationService`：读取任务并复用规则评估器；
- `IncidentService`：Incident 创建、关联、统计和状态机；
- `IncidentEvaluationRunner`：有界轮询持久化评估任务；
- `NotificationOutboxService`：通知任务与重试；
- `FeishuClient`：租户 Token、消息卡片、回复和更新；
- `FeishuEventService`：事件验证、消息回流和卡片动作；
- `IncidentQueryService`：列表与详情读取模型。

新增物理表（旧迁移已占用 `incidents`、`incident_activities` 和 `incident_alert_links`，新模型不得复用旧表）：

- `operational_incidents`
- `operational_incident_alerts`
- `operational_incident_activities`
- `incident_evaluation_jobs`
- `incident_notification_routes`
- `incident_notification_outbox`
- `incident_feishu_threads`
- `feishu_event_receipts`
- `operational_incident_operations`：只保存操作范围、幂等键哈希、命令指纹、结果版本和安全审计字段，不保存请求正文。

所有批次、文本和 JSON 字段必须有界。Worker 不读取故障注入平台，不使用 `scenario_id`、`experiment_id` 或任何实验身份。

## 9. API

平台页面：

- `GET /api/v1/incidents`
- `GET /api/v1/incidents/{incident_id}`
- `POST /api/v1/incidents/{incident_id}/acknowledge`
- `POST /api/v1/incidents/{incident_id}/resolve`
- `GET /api/v1/incident-notification-routes`
- `POST /api/v1/incident-notification-routes`
- `PATCH /api/v1/incident-notification-routes/{route_id}`

飞书回调：

- `POST /api/v1/integrations/feishu/events`
- `POST /api/v1/integrations/feishu/card-actions`

管理写操作使用 Bearer Token、期望版本和幂等键。飞书回调使用飞书签名与事件身份验证，不接受平台 Bearer Token 替代。

## 10. 前端产品结构

左侧导航新增“Incident 中心”，与“告警中心”“Incident 规则”“接入源管理”平级。

### 10.1 Incident 列表

默认只展示 `OPEN` 和 `ACKNOWLEDGED`，支持状态、环境、严重级别和关键词筛选。每行集中展示编号与标题、状态、环境与聚合对象、来源规则、关联 Alert 数和最近更新时间。页面不添加无关统计卡。

### 10.2 Incident 详情

顶部展示编号、标题、状态和“确认事故/解决事故”操作。正文包含：

- 当前情况：状态、严重级别、环境、对象、持续时间、活动 Alert 数；
- 自动创建依据：来源规则版本和中文条件摘要；
- 关联 Alert：紧凑列表并可展开 Alert 已有详情；
- 处置时间线：系统事件、页面操作和飞书沟通按时间排序；
- 飞书协同：群名称、线程状态、最近同步、失败原因和打开线程入口。

### 10.3 飞书配置

从 Incident 中心进入轻量配置面板，按环境维护一个群路由。页面检测后端是否配置应用凭据，但绝不读取或显示 Secret。

视觉以用户已确认的产品图为准：深色石墨控制台、紧凑列表、三栏详情和飞书原生浅色卡片。实现前不再创建演示数据。

## 11. 错误处理与可观测性

- 规则评估失败：Alert 已保存，任务重试，健康页显示失败数量；
- Incident 并发创建冲突：读取已存在 unresolved Incident 并继续关联；
- 通知路由缺失：Incident 标记未配置，不重试；
- 飞书鉴权失败：Outbox 进入失败，页面给出可操作原因；
- 飞书事件重复：返回成功但不重复写时间线或改变状态；
- 版本冲突：页面和卡片提示 Incident 已更新并重新加载；
- MySQL 不可用：保持现有安全失败，不宣称已接收或已通知。

## 12. 测试与交付顺序

1. Incident 领域模型、状态机与并发去重；
2. Alert 事务 Outbox 与实时规则评估；
3. Incident 查询 API 和页面；
4. 通知路由与 FeishuClient 契约测试；
5. 飞书事件、线程回流与卡片动作；
6. 失败重试、健康状态和完整浏览器流程；
7. 使用飞书测试应用和测试群进行真实联调。

自动化测试覆盖状态转换、规则实时/历史一致性、重复任务、并发创建、跨环境隔离、Alert 恢复、通知幂等、飞书重试、伪造回调、群消息过滤和前端所有核心状态。真实飞书联调必须由用户提供测试应用凭据和测试群，不在源码或测试数据中保存 Secret。
