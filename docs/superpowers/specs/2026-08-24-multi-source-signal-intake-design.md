# 多源信号接入与告警投影设计

## 1. 目标与边界

本设计为 Incident Intelligence 增加两个真实外部来源：Alertmanager Webhook v4 与 CloudEvents 1.0 HTTP。两个适配器只负责把外部事实转换成统一 SignalCommand，共享接入服务负责幂等保存 SignalEvent、维护 Alert 当前状态投影和追加审计。

本阶段不创建 Incident 或 DiagnosisRun。收到告警不等于发生事故，告警恢复也不等于事故关闭。服务目录、事故候选过滤和可解释关联必须在后续规格中独立实现。

## 2. 方案选择

采用“共享接入核心 + 薄适配器”。

- Alertmanager Adapter：认证、Webhook v4 批次校验、字段映射；
- CloudEvents Adapter：认证、结构化或 Binary 协议解析、受限 data 校验；
- Signal Intake Service：来源事件幂等、SignalEvent 保存、Alert 投影、乱序规则、审计和事务；
- Record Repositories：带锁 Alert 查询、SignalEvent 与审计写入、投影更新；
- PostgreSQL：保存规范化事实，不保存原始外部负载。

不采用两套独立业务链路，避免幂等和状态规则分叉。不采用原始事件先落盘再异步转换，避免引入无界负载、敏感数据保留和当前阶段不需要的消息基础设施。

## 3. 通用内部命令

两个适配器必须输出相同的不可变 SignalCommand：

- `source`：`alertmanager` 或 `cloudevents`；
- `source_instance`：规范化来源 URI 的 SHA-256 十六进制摘要；
- `source_event_id`：外部事件稳定身份的 SHA-256 十六进制摘要；
- `source_alert_key`：来源内部稳定告警键，长度 1–128；
- `event_type`：`alert.firing` 或 `alert.resolved`；
- `title`、`summary`、`severity`、`service`、`environment`；
- `event_at`：用于状态排序的 UTC 时间；
- `episode_started_at`：本轮告警开始时间；
- `facts`：最多 20 个、经过白名单与脱敏的有界键值；
- `normalization_reason_codes`：适配器采用默认值或丢弃字段时使用的固定原因码。

共享服务在接收命令后再次执行禁止身份检查。任何适配器不得绕过该服务直接写领域表。

## 4. 领域模型与迁移

### 4.1 SignalEvent

SignalEvent 增加非空 `event_type`。取值首期限定为：

- `manual.reported`；
- `alert.firing`；
- `alert.resolved`。

现有人工报告数据迁移为 `manual.reported`。`source_event_id` 继续承担来源事件唯一性，但读取 API 不暴露该值。

### 4.2 Alert

Alert 增加：

- `source`；
- `source_instance`；
- `source_alert_key`；
- `state_changed_at`。

数据库增加唯一约束 `(source, source_instance, source_alert_key)`。现有 `signal_event_id` 定义为“最近一次真正改变 Alert 投影的 SignalEvent”。迟到但未改变投影的 SignalEvent 不覆盖该字段。

`first_observed_at` 表示当前轮次开始时间；`last_observed_at` 表示最近一次被投影接受的事件时间；`state_changed_at` 表示当前状态开始时间。每次投影内容或状态有效变化时 `version + 1`。

现有人工报告 Alert 回填：

- `source = manual`；
- `source_instance = SHA-256("manual")`；
- `source_alert_key = 对现有来源身份计算的不可逆摘要`；
- `state_changed_at = created_at`。

迁移不得在新列中复制可读取的人工幂等键。

## 5. Alertmanager 适配器

### 5.1 接口与认证

- 接口：`POST /api/v1/intake/alertmanager`；
- 凭据：`II_ALERTMANAGER_TOKEN`；
- Bearer Token 使用字节恒定时间比较；
- 最多 256 KiB，最多 100 条 alerts；
- 整批先完成认证、容量、Schema、禁止身份和字段映射校验。

任意一条无效时返回稳定错误，整批不写数据库。全部有效后在一个事务中处理；任一步失败整批回滚。

### 5.2 来源与幂等身份

Webhook `externalURL` 去除用户信息、查询和片段，保留规范化 scheme、host、port、path 后计算 `source_instance` 摘要。保留 path 用于区分同一网关后的不同 Alertmanager 实例。规范化前先执行容量和 URI 校验，原始值不持久化。

每条 alert 的 `source_event_id` 由以下规范化字段计算摘要：来源实例、fingerprint、状态、startsAt、endsAt、被接受的 labels、被接受的 annotations 映射结果。数组顺序、JSON 键顺序和 Webhook 分组变化不得改变结果；真实内容变化必须形成新的 SignalEvent。

`fingerprint` 是 `source_alert_key`。

### 5.3 字段映射

- 必填：fingerprint、startsAt、status、`labels.service`；
- environment：取 `labels.environment`，缺失使用 `unknown`；
- title：优先 `annotations.summary`，其次 `labels.alertname`，两者均无则拒绝；
- summary：优先 `annotations.description`，其次使用 title 生成有界摘要；
- status：只接受 `firing`、`resolved`；
- firing 的 `event_at` 使用 startsAt；
- resolved 必须有有效 endsAt，`event_at` 使用 endsAt；
- episode_started_at 始终使用 startsAt；
- event_at 不得超过服务器当前时间五分钟。

严重度固定映射：

- `critical`、`page`、`p1` → critical；
- `high`、`p2` → high；
- `warning`、`warn`、`medium`、`p3` → medium；
- `info`、`low`、`p4`、`p5` → low；
- 缺失或未知 → medium，并加入 `severity_defaulted` 原因码。

只保留审核白名单标签，最多 20 个。生成器 URL、任意查询、任意 annotations 和 Webhook 原始正文不保存。

## 6. CloudEvents 适配器

### 6.1 接口与认证

- 接口：`POST /api/v1/intake/cloudevents`；
- 凭据：`II_CLOUDEVENTS_TOKEN`；
- 请求体最多 64 KiB；
- 每次只接收一个 CloudEvent；
- 支持 `application/cloudevents+json` 结构化模式；
- 支持 HTTP Binary 模式，必需请求头为 `ce-specversion`、`ce-id`、`ce-source`、`ce-type`、`ce-time`，可选 `ce-subject`，正文是 JSON data。

### 6.2 受限事件类型

只接受 `specversion = 1.0` 和 `type = com.incidentintelligence.alert.v1`。标准上下文字段之外的扩展属性首期拒绝，避免未经审核的数据进入平台。

data 只允许：

- `alert_key`：长度 1–128；
- `title`：长度 1–200；
- `summary`：长度 1–2000；
- `severity`：critical、high、medium、low；
- `service`：长度 1–128；
- `environment`：production、staging、development、unknown；
- `status`：firing、resolved；
- `started_at`：UTC 感知时间；
- `labels`：最多 20 个有界白名单键值。

CloudEvent `time` 是 `event_at`，data.started_at 是 `episode_started_at`。事件时间不得超过服务器当前时间五分钟。

### 6.3 来源与幂等身份

`source_instance = SHA-256(规范化 source URI)`，不保存原始 source URI。`source_event_id = SHA-256(规范化 source URI + NUL + id)`，因此同一来源的同一 CloudEvent ID 稳定重放，不同来源的相同 ID 不碰撞。

data.alert_key 是 `source_alert_key`。subject 只参与输入一致性检查，不持久化；subject 存在时必须与 data.service 一致。

## 7. Alert 投影规则

共享服务先按 `(source, source_instance, source_alert_key)` 锁定或创建 Alert，再应用以下规则：

1. 不存在投影且收到 firing：创建 ACTIVE；
2. 不存在投影且收到 resolved：保存 SignalEvent，但不凭空创建 RESOLVED Alert，审计 `orphan_resolved_ignored`；
3. ACTIVE 收到同一轮次、时间不早于现有投影的 firing：刷新规范化内容并递增版本；
4. ACTIVE 收到 event_at 不早于 state_changed_at 的 resolved：切换 RESOLVED；
5. RESOLVED 收到 episode_started_at 不晚于 state_changed_at 的 firing：视为迟到，只保存 SignalEvent；
6. RESOLVED 收到 episode_started_at 晚于 state_changed_at 的 firing：新一轮重开为 ACTIVE；
7. event_at 早于 last_observed_at 的事件只保存 SignalEvent，不覆盖投影；
8. 同一 event_at 发生 firing 与 resolved 冲突时 resolved 优先；
9. 完全重复 source event 直接重放，不新增 SignalEvent、Alert 版本或审计。

并发创建相同 Alert 身份时依靠唯一约束和事务重读收敛为一条投影。实现必须避免丢失更新，并使用行锁或等价数据库并发控制。

Alertmanager 不提供同一轮 firing 内容更新的独立发生时间。对于相同 episode_started_at 和 event_at 的不同 firing 内容，投影的标题、摘要和规范化标签按平台接收顺序采用最后一次送达值；该规则只影响展示内容，不得把 RESOLVED 状态倒退为 ACTIVE。此限制必须在审计原因码和运行说明中明确记录。

## 8. 审计与响应

固定审计动作：

- `signal.received`；
- `alert.opened`；
- `alert.updated`；
- `alert.resolved`；
- `alert.reopened`；
- `alert.stale_signal_ignored`；
- `alert.orphan_resolved_ignored`。

审计详情只包含资源 ID、父 ID、适配器类型和固定原因码。不得保存请求头、Token、外部 URI、标题、摘要、标签或原始正文。幂等重放不追加审计。

Alertmanager 成功响应按输入顺序返回每条结果的 signal_event_id、可选 alert_id 和 outcome，并汇总 created、updated、resolved、reopened、stale、orphan_resolved、replayed 数量。CloudEvents 返回同样的单条结果结构。响应整体有界且不返回原始输入。

## 9. 错误契约

稳定错误码包括：

- `authentication_required`：401；
- `request_too_large`：413；
- `batch_too_large`：422；
- `unsupported_event_type`：422；
- `validation_error`：422；
- `forbidden_identity`：422；
- `source_event_conflict`：409，同一来源事件身份对应不同规范化内容；
- `persistence_unavailable`：503。

错误响应不回显被拒绝值。认证、校验或数据库失败不记录原始正文和 Secret。

## 10. 测试与验收

### 10.1 领域与迁移

- 新字段约束、唯一投影身份和现有人工数据回填；
- 迁移升级、降级和 ORM 元数据一致性；
- 三套状态机仍独立，外部接入不创建 Incident 或 DiagnosisRun。

### 10.2 共享服务

- 首次 firing、更新、resolved、迟到、重开、同时间冲突和孤立 resolved；
- 相同事件重放和相同身份不同内容冲突；
- 并发 SignalEvent 幂等与 Alert 唯一；
- 批次原子回滚和审计内容边界。

### 10.3 Alertmanager

- 合法单条与多条批次；
- 严重度映射与默认原因码；
- 完全重复、分组变化、内容更新和恢复；
- 缺失 fingerprint、service、无效时间、超过 100 条和超过 256 KiB；
- 禁止身份、生成器 URL和未审核字段不持久化。

### 10.4 CloudEvents

- 结构化与 Binary 模式得到相同内部结果；
- source + id 幂等与 alert_key 投影；
- 不支持类型、扩展属性、subject 不一致、未来时间和容量上限；
- 独立 Token 交叉使用失败。

### 10.5 安全与真实冒烟

- 日志、响应、数据库和审计中不存在 Token、完整请求、来源 URI、查询和禁止实验身份；
- 数据库故障不会留下部分 SignalEvent、Alert 或审计；
- 使用真实 Alertmanager 格式与两种 CloudEvents 内容模式完成本地 HTTP 冒烟；
- 全量 Ruff、格式、Mypy、迁移、测试和覆盖率门槛通过后才能标记已实现。

## 11. 明确留待后续

本设计不包含服务目录、拓扑、维护窗口、事故候选、关联解释、Incident 创建、DiagnosisRun 创建、自动取证或 AI。外部告警进入 Alert 后暂时停留在告警层，直到后续关联规格实施。
