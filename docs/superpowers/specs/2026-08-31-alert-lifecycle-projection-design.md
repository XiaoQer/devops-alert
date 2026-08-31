# Alert 生命周期投影设计

## 状态

产品设计已确认，等待规格审阅。

## 背景

平台当前把 Alertmanager 的 `firing` 与 `resolved` 分别保存为不可变 SignalEvent，并直接使用 SignalEvent 展示列表和统计趋势。同一次 Prometheus 告警因此会在页面中出现两条记录，恢复通知也会使告警数量增加。这种统计口径不能作为后续 Incident 判断的输入。

本次改造在 SignalEvent 与未来 Incident 之间新增独立 Alert 模型。一个 Alert 表示一次完整或部分完整的告警周期；`resolved` 更新对应 Alert，不创建第二条业务告警。SignalEvent 继续承担接收事实、传输幂等和审计职责，不作为用户侧告警数量的统计对象。

## 目标

- 一次 `firing → resolved` 生命周期只形成一条 Alert；
- 同一告警恢复后再次触发时形成新的 Alert；
- 告警名称、摘要和描述使用独立字段；
- 告警列表、详情、数量和趋势统一读取 Alert；
- 保持多来源输入、乱序通知、重复通知和并发接收安全；
- 为后续 Incident 设计提供状态明确、数量准确的输入，但本次不实现 Incident。

## 非目标

- Incident 创建、关联或运营；
- 告警聚类、批次、风暴收敛或文本相似度；
- 自动取证、AI 分析或自动修复；
- 确认、抑制、关闭等处置状态；
- 删除 SignalEvent 或接收审计数据；
- 恢复旧告警分组与事故实现。

## 领域模型

### SignalEvent

SignalEvent 继续表示平台接收到的一次不可变外部事实。`firing` 和 `resolved` 可以各自形成 SignalEvent，用于：

- 来源事件身份幂等；
- 输入冲突保护；
- 接收回执与审计；
- Alert 投影失败后的安全重试和历史回放。

SignalEvent 不直接进入告警列表、告警数量、趋势或未来 Incident 判断。

### Alert

Alert 表示一次告警周期，使用独立模型和持久化表。首期状态只有：

- `ACTIVE`：告警中；
- `RESOLVED`：已恢复。

核心字段：

- `id`：平台 Alert ID；
- `alert_source_id`：接入源 ID；
- `source_alert_key`：来源侧稳定告警实例标识，Alertmanager 使用 fingerprint；
- `episode_started_at`：本次告警周期开始时间；
- `alert_name`：稳定告警名称；
- `summary`：简短摘要；
- `description`：详细描述；
- `state`：`ACTIVE` 或 `RESOLVED`；
- `severity`、`environment`、`service`；
- `entity_type`、`entity_key`、`entity_display_name`；
- `first_observed_at`、`last_observed_at`；
- `first_received_at`、`last_received_at`；
- `resolved_at`；
- `firing_observed`：是否观测到 firing，用于识别只有 resolved 的不完整周期；
- `created_at`、`updated_at`、`version`。

Alert 的周期唯一键为：

```text
(alert_source_id, source_alert_key, episode_started_at)
```

同一 fingerprint 在不同开始时间触发时属于不同周期，必须创建不同 Alert。

## 字段规范化

### Alertmanager

- `alert_name`：精确使用 `labels.alertname`；
- `summary`：使用 `annotations.summary`，缺失时为空；
- `description`：使用 `annotations.description`，缺失时为空；
- `source_alert_key`：使用 Alertmanager fingerprint；
- `episode_started_at`：使用 `startsAt`；
- `resolved_at`：resolved 通知使用 `endsAt`。

Alertmanager 缺少或提供空白 `labels.alertname` 时按字段校验失败处理，不使用 summary 代替。

`summary` 不再作为告警名称。以本次真实数据为例：

```text
alert_name = MySQLRowLockWaitsIncreasing
summary = MySQL 新增行锁等待
```

### CloudEvents 与其他来源

适配器必须输出统一的 `alert_name`：

1. 优先读取规范化字段 `alert_name`；
2. 缺失时使用 CloudEvents `type`；
3. 仍无法获得时，使用来源类型和稳定事件类型生成名称；
4. 不强制非 Prometheus 来源提供 `labels.alertname`；
5. `summary` 和 `description` 始终独立，不能替代 `alert_name`。

所有适配器仍需输出 `source_alert_key`、`episode_started_at` 和生命周期事件类型，以便使用同一套 Alert 投影规则。

## 生命周期规则

### 收到 firing

1. 根据周期唯一键查找 Alert；
2. 不存在时创建 `ACTIVE` Alert，告警总数增加一；
3. 已存在时更新允许变化的规范化内容、最后观测时间和最后接收时间；
4. 重复 firing 不增加告警数量；
5. 相同来源告警已恢复但新的 `episode_started_at` 不同时，创建新的 Alert。

### 收到 resolved

1. 根据周期唯一键查找 Alert；
2. 找到时更新原 Alert 为 `RESOLVED`，写入 `resolved_at`、最后观测时间和最后接收时间；
3. resolved 不增加告警数量；
4. 未找到 firing 时创建一条 `RESOLVED` Alert，并设置 `firing_observed=false`，避免恢复事实丢失；
5. 后续同周期 firing 晚到时补全该 Alert，并把 `firing_observed` 更新为 true，不创建第二条 Alert。

### 状态约束

- 同一周期不允许从 `RESOLVED` 退回 `ACTIVE`；
- 乱序晚到的 firing 只能补全字段，不能抹掉恢复状态或恢复时间；
- `resolved_at` 不得早于 `episode_started_at`；
- 重放相同 SignalEvent 必须返回已有结果，不重复修改 Alert 版本。

## 事务、并发和幂等

- 每个首次接收的 SignalEvent 写入与对应 Alert 投影在同一个 MySQL 事务中完成；
- SignalEvent 或 Alert 任一操作失败时整笔回滚，由发送方安全重试；
- Alert 周期唯一键承担并发收敛的最后防线；
- 并发创建冲突时读取已存在 Alert，再按同一事件重放更新规则处理；
- 不使用进程内锁作为正确性保证；
- 接收回执必须区分新建 Alert、更新 Alert、精确重放和处理失败，但不得返回 Secret 或无界原始请求。

## API 与前端

现有业务路径保持不变，但语义调整为 Alert：

- `GET /api/v1/alerts`：分页读取 Alert；
- `GET /api/v1/alerts/{alert_id}`：读取单个 Alert；
- `GET /api/v1/alerts/timeseries`：按 Alert 首次接收时间统计新告警周期。

告警中心每个 Alert 只显示一行，至少展示：

- 状态；
- 告警名称 `alert_name`；
- 摘要；
- 级别；
- 服务或资源；
- 接入源；
- 触发时间；
- 恢复时间；
- 持续时间。

页面不把 firing 和 resolved 展开成两条业务告警。完整 SignalEvent 审计不进入首期告警中心 UI。

## 统计口径

- 告警总数：Alert 周期数量；
- 当前告警数：`state=ACTIVE`；
- 已恢复数量：`state=RESOLVED`；
- 趋势图：按 `first_received_at` 统计新建 Alert；
- resolved 仅改变状态，不增加告警总数或趋势数量；
- 时间筛选默认使用 `first_received_at`；
- 持续时间：已恢复时为 `resolved_at - episode_started_at`，活动中为当前时间减去 `episode_started_at`，仅用于展示，不持久化动态值。

## 历史数据回放

现有 SignalEvent 按以下顺序重建 Alert：

1. 按 `event_at`、`received_at` 和稳定 ID 排序；
2. 使用周期唯一键归并；
3. 按生命周期规则依次应用 firing 和 resolved；
4. 对缺少 firing 或 resolved 的周期保留不完整标记；
5. 回放任务可重复执行，不能重复创建 Alert；
6. 回放前后输出有界审计计数，包括扫描事件数、新建 Alert 数、更新 Alert 数、不完整周期数和失败数。

历史回放不删除或修改 SignalEvent。

## 失败处理

- 数据库不可用时接收请求失败并回滚，不产生半条 Alert；
- 单条负载字段不合法时维持现有安全拒绝和接收回执；
- resolved 无对应 firing 时安全降级为不完整的 `RESOLVED` Alert；
- 投影或回放失败不伪造成功状态；
- API 读取失败不影响后续来源接收；
- 不完整生命周期必须可筛选和审计，但不在默认列表制造额外告警数量。

## 测试策略

### 领域与服务测试

- firing 创建一条 ACTIVE Alert；
- 重复 firing 不增加数量；
- resolved 更新原 Alert，数量保持不变；
- 恢复后新的开始时间创建新 Alert；
- resolved 先到时创建不完整 RESOLVED Alert；
- firing 晚到时补全但不把状态退回 ACTIVE；
- 同周期并发创建最终只有一条 Alert；
- SignalEvent 与 Alert 任一写入失败时事务整体回滚。

### 适配器测试

- Alertmanager `alert_name` 精确来自 `labels.alertname`；
- summary 和 description 独立保存；
- CloudEvents 缺少 `alert_name` 时按规则生成稳定名称；
- fingerprint 与 startsAt 正确形成周期身份。

### API 与前端测试

- 列表、详情和趋势读取 Alert 而非逐条 SignalEvent；
- firing 后总数增加一，resolved 后总数不变；
- 状态从告警中更新为已恢复；
- 告警名称显示 `MySQLRowLockWaitsIncreasing`，摘要显示“MySQL 新增行锁等待”且两者不混用；
- 趋势只在 Alert 首次创建时增加；
- 筛选、分页、时间拖选和接入源聚合保持可用。

### 历史回放测试

- 现有 firing/resolved 合并为一条 Alert；
- 多次执行回放结果一致；
- 不完整、乱序和并发数据均安全收敛；
- 回放失败可审计且不会留下部分提交批次。

## 验收场景

以 `MySQLRowLockWaitsIncreasing` 为例：

1. 09:45:19 firing 到达后创建一条 ACTIVE Alert；
2. 页面告警名称显示 `MySQLRowLockWaitsIncreasing`，摘要显示“MySQL 新增行锁等待”；
3. 09:45:49 resolved 到达后更新同一 Alert 为 RESOLVED；
4. 告警总数和趋势数量仍为一；
5. 页面显示恢复时间和约 30 秒持续时间；
6. 该规则以后再次 firing 且 startsAt 不同时创建第二条 Alert；
7. SignalEvent 审计仍保留本次 firing 和 resolved 两个接收事实。

## 实施边界

本规格完成后，平台的数据流为：

```text
Alertmanager / CloudEvents
          ↓
不可变 SignalEvent
          ↓
一次生命周期一条 Alert
          ↓
告警列表、状态和趋势
```

Incident 的生成条件、关联规则、候选状态和页面表现必须在 Alert 投影验收通过后另建规格，不在本次实现中提前固化。
