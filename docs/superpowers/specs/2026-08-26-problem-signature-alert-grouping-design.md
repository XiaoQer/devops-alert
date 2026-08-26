# 问题签名告警归集设计

## 设计结论

告警归集从“资源实例相同才归组”升级为“问题签名相同才归组”。Pod、Node、Instance 等实体描述受影响对象，不再直接决定问题边界。平台只使用告警已携带的标签和注解，不查询 Kubernetes、Prometheus 或其他外部系统。

## 三层边界

1. SignalEvent 保留一次外部事实；
2. Alert 使用来源稳定身份去重同一告警的触发、更新和恢复；
3. AlertGroup 使用问题签名压缩同一时间段、同一影响范围内的相似 Alert。

告警归组不直接决定事故边界。不同告警组仍由事故关联引擎根据服务、时间、拓扑和运营状态判断是否属于同一 Incident。

## 问题签名

问题签名版本固定为 `problem-signature.v1`，输入均为规范化后的安全字段：

- 逻辑来源：`alert_source_id`，避免不同来源的同名规则被误合并；
- 问题类型：优先 `alertname`，缺失时使用规范化标题，最后回退为“未命名告警”；
- 症状：使用现有标准化症状，允许 `unknown`；
- 环境：使用现有 environment，允许 `unknown`；
- 影响范围类型和范围键；
- 签名版本。

以上字段使用带版本前缀的 SHA-256 生成 `problem_key`。原始 Webhook、未知标签和 Secret 不进入签名或持久化。

## 影响范围推导

范围只由告警已提供的标签确定，优先级如下：

1. service → SERVICE；
2. cluster + namespace + workload，或 namespace + workload → WORKLOAD；
3. cluster + namespace，或 namespace → NAMESPACE；
4. cluster → CLUSTER；
5. namespace + job，或 job → JOB；
6. 其他 → SOURCE，以告警源身份作为最小安全边界。

Pod、Node、Instance 和 Container 是影响资源，不进入范围键。这样同一 Namespace 内同时出现的多条 `KubePodNotReady` 可以形成一个问题组，同时组内保留每个 Pod 的独立成员和资源标识。

## 归组规则

候选必须同时满足：

- `problem_key` 相同；
- 告警组仍为 ACTIVE；
- 当前 Alert 尚未被固定在其他事故冲突组中；
- 位于对应范围的活动窗口内。

首版窗口固定为：

- SERVICE、WORKLOAD、JOB、NAMESPACE、CLUSTER：300 秒；
- SOURCE：120 秒。

存在唯一候选时加入；无候选时创建新组；多个候选时为避免误并创建新组并记录歧义。已属于 `problem-signature.v1` 组的告警保持原关系。`unknown` 症状不再单独阻止归组，因为问题类型和范围仍能提供确定性边界。

## 数据模型

SignalEvent、Alert 继续保存真实实体摘要，不改变其资源语义。AlertGroup 新增：

- `problem_key`：64 位摘要；
- `problem_type`：可展示的问题类型；
- `scope_type`：SERVICE、WORKLOAD、NAMESPACE、CLUSTER、JOB 或 SOURCE；
- `scope_key`：64 位摘要；
- `scope_display_name`：安全、有界的范围名称；
- `signature_version`：`problem-signature.v1`。

现有 AlertGroup 的实体字段继续描述代表性影响资源，不能冒充问题签名。

## 现有数据升级

数据库迁移只增加和回填新字段，不删除 SignalEvent、Alert、告警组、成员或审计。回填从告警组代表 Alert 及其最新 SignalEvent 的已持久化安全字段计算；历史缺少 alertname 时使用标题。

运行期提供有界的重新归组操作，只处理当前 ACTIVE、service 为空、尚未关联 Incident 且仍使用旧归组规则的告警组。重新归组为一个可审计事务：创建或选择 v1 问题组、移动当前成员、刷新计数，把空旧组标记为 RESOLVED，并记录旧组、新组、成员数和固定原因码。已关联事故或已解决历史组不自动重写。

## UI 表达

告警组卡片和详情优先显示问题类型、影响范围、受影响资源数、原始告警数、状态、持续时间和事故关联状态。service 为空时显示“服务未提供”，不能显示“未知服务”。资源和范围缺失时分别显示“对象未提供”和“范围未提供”。

## 安全退化

- 无法形成安全问题类型时只在 SOURCE 范围、120 秒内归组；
- 多个候选不自动合并；
- 无 service 告警组不创建虚假 Incident，保存 `SKIPPED_SERVICE_MISSING` 关联决策；
- 归组失败不影响原始 Alert 的接收、查询和人工查看；
- 不访问 Kubernetes，不新增密钥、RBAC 或异步 service 解析。

## 验证重点

- 6 条同来源、同 Namespace、不同 Pod 的 `KubePodNotReady` 收敛为 1 个组和 6 个资源；
- 不同 Namespace、不同 alertname、不同环境或不同来源不会误合并；
- 有 service 的既有同服务、同症状风暴行为保持；
- unknown 症状在问题类型和范围一致时仍可归组；
- 无 service 组不创建 Incident，并产生可读跳过决策；
- 现有活动无事故组可安全重新归组，已关联事故和历史已解决组不被重写。
