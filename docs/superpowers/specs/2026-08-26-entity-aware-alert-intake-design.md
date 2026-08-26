# 面向实体的告警接入与服务识别设计

## 1. 问题定义

Alertmanager 传递的是监控规则计算出的标签集合，不保证存在统一的业务 `service` 标签。Kubernetes 基础告警经常只提供 `namespace + pod`，节点告警只提供 `node`，Prometheus 自监控告警可能只有 `job` 或 `pod`。把 `service` 作为接入必填项，会把来源标签治理问题放大成数据丢失问题。

本设计将“接收告警”“识别主要实体”“解析业务服务”“关联事故”拆成四个独立阶段。任何后置阶段失败都不能撤销已经接收的告警事实。

## 2. 方案选择

### 方案 A：要求所有 PrometheusRule 补充 service

优点是现有平台改动最少。缺点是 kube-prometheus-stack 的内置规则数量多、升级会覆盖改动，而且 Node、Cluster、控制面等告警本来就没有自然的业务服务。该方案不采用。

### 方案 B：缺少 service 时用 job、namespace 或 Pod 名称代替

实现简单，但会制造虚假服务、污染服务目录和事故关联。Pod 名称还包含动态哈希，无法形成稳定身份。该方案不采用。

### 方案 C：实体先行，服务异步解析

协议合法的告警先落库；适配器确定性提取资源实体；可选解析器再通过受信只读数据源补充服务。解析成功后重新进入归组和事故关联，解析失败时告警仍可运营。该方案是本设计的选择。

## 3. 领域模型

### 3.1 主要实体

新增有限枚举：

- `SERVICE`：告警显式携带业务服务；
- `WORKLOAD`：告警携带 Deployment、StatefulSet、DaemonSet 等工作负载身份；
- `POD`：具有 `namespace + pod`；
- `NODE`：具有 `node`；
- `JOB`：具有 `namespace + job` 或独立 `job`；
- `INSTANCE`：具有 `instance`；
- `CLUSTER`：具有 `cluster`；
- `UNKNOWN`：没有足够身份线索。

实体键由版本化确定性函数生成，采用规范化类型和真实标签组合。例如 `POD` 使用 `namespace + pod`，`NODE` 使用 `cluster + node`。API 返回显示名称和类型，不暴露内部摘要算法。

### 3.2 服务识别状态

- `RESOLVED`：通过显式告警标签或受信解析器确认服务；
- `PENDING`：具有可供解析的实体线索，等待解析或重试；
- `UNRESOLVED`：解析已完成但没有可信服务；
- `NOT_APPLICABLE`：该实体本身是节点、集群或监控管道，当前不要求业务服务。

同时保存：

- `service_resolution_source`：`ALERT_LABEL`、`KUBERNETES_POD_LABEL` 或空；
- `service_resolution_confidence`：`HIGH`、`MEDIUM` 或空；
- `service_resolution_reason_codes`：固定、有界原因码；
- `service_resolved_at`：成功解析时间。

SignalEvent 保存接入时的不可变实体和解析初态。Alert 保存当前解析投影。后续解析不得改写 SignalEvent。

## 4. 接入与实体提取

Alertmanager 适配器先将顶层 `commonLabels` 与单条 `labels` 合并，单条标签优先。标题仍由 `annotations.summary` 或 `alertname` 提供；缺少标题仍拒绝，因为无法形成可运营告警。

实体提取优先级：

1. 非空 `service` → SERVICE / RESOLVED；
2. 非空 `workload` 和可选 `workload_kind` → WORKLOAD / PENDING；
3. `namespace + pod` → POD / PENDING；
4. `cluster + node` 或 `node` → NODE / NOT_APPLICABLE；
5. `namespace + job` 或 `job` → JOB / PENDING；
6. `instance` → INSTANCE / PENDING；
7. `cluster` → CLUSTER / NOT_APPLICABLE；
8. 其他 → UNKNOWN / UNRESOLVED。

`app.kubernetes.io/name` 和 `app` 只有在它们真实存在于告警标签时才可直接解析服务；`component` 只作为事实，不单独确认业务服务。

整个 Webhook 仍保持一个事务、幂等、容量限制和批次回滚。缺少服务不再是校验错误。

## 5. 持久任务与 Kubernetes 解析器

新增 `entity_resolution_jobs`。Alert 首次进入 PENDING 或实体线索发生有效变化时，在同一接入事务创建或合并一个活动任务。任务沿用现有任务的 PENDING、LEASED、COMPLETED、FAILED 状态、租约、过期接管、尝试上限和有界审计模式。

Kubernetes 解析器是可选只读组件，不在接入请求内执行。首版仅处理 POD：

1. 使用任务中已保存的 `namespace + pod`；
2. 调用 Kubernetes CoreV1 Pod 读取接口；
3. 优先读取 `app.kubernetes.io/name`，其次读取 `app`；
4. 找到非空标签后以 `KUBERNETES_POD_LABEL/HIGH` 更新 Alert；
5. 未找到 Pod、权限不足、超时、标签缺失分别记录固定原因码；
6. 更新成功时，使旧的未解析告警组成员失效，并创建新的归组任务；
7. 解析器不可用时任务延迟重试，告警读取和人工操作继续可用。

解析器只接收有界实体线索，不读取 Alertmanager 原始请求。Kubernetes 凭据只存在于运行环境，不进入数据库、日志或 API。

## 6. 告警归组和事故关联

AlertGroup 增加主要实体类型和实体键，`service` 改为可空。归组键从“服务 + 环境 + 症状 + 时间窗口”调整为“实体键 + 环境 + 症状 + 时间窗口”。已有服务告警的实体键由服务生成，因此原有行为保持不变。

未识别服务的告警组仍能压缩相同 Pod、Node、Job 或 Cluster 的告警风暴。事故关联阶段执行门槛：

- `service_resolution_status=RESOLVED` 且服务目录存在时，执行现有服务关联；
- 其他情况保存 `SKIPPED_UNRESOLVED_SERVICE` 或 `SKIPPED_SERVICE_NOT_APPLICABLE`，不创建虚假事故；
- 服务后来解析成功时生成新组级关联任务，不修改历史决策。

本阶段 Incident 仍以业务服务为主实体，避免同时重写事故中心、服务目录和关联算法。任意实体事故是后续独立规格。

## 7. API 与前端

告警列表、详情和告警组增加：

- 主要实体类型与中文名称；
- 服务名称可空；
- 服务识别状态中文文案；
- 识别依据，例如“告警标签”“Kubernetes Pod 标签”；
- 待识别或失败的固定中文说明。

展示规则：

- 已识别：`服务：aegis-springboot-demo`；
- 识别中：`主要对象：Pod / devops-platform/example-pod`，`服务：正在识别`；
- 无法识别：保留主要对象并展示原因，不显示 `unknown-service`；
- 不适用：Node 或 Cluster 告警显示基础设施实体，不提示用户补 service。

筛选器新增“服务识别状态”，服务筛选只匹配已识别服务。搜索同时匹配标题、服务和实体显示名称。

## 8. 迁移和兼容

新迁移从当前 `0006_alert_grouping_storm` 前进。现有非空服务数据回填为 SERVICE / RESOLVED / ALERT_LABEL，并生成稳定实体键；随后才把 SignalEvent、Alert 和 AlertGroup 的服务列改为可空。降级前必须验证不存在空服务记录，否则以明确错误停止，避免伪造数据。

CloudEvents 和人工报告当前仍要求服务，行为不在本阶段放宽。外部 API 只增加字段或把服务响应声明改为可空，不删除现有字段。动态 Alertmanager 来源和兼容入口共享同一个适配器行为。

## 9. 失败处理与安全

- 接入数据库失败仍整批回滚；
- 单条解析任务失败不影响同批其他任务；
- 解析更新使用 Alert 版本和行锁，过期任务不覆盖新投影；
- Kubernetes 响应只提取两类白名单标签，不保存完整 Pod 对象；
- 日志和审计只保存固定结果码、规范化 ID 和耗时类别；
- Secret、kubeconfig、Bearer Token 和 Kubernetes 响应正文不得进入持久化或 API；
- 禁止实验身份扫描继续覆盖所有新字段。

## 10. 测试策略

按测试驱动分层验证：

1. 领域测试：实体优先级、稳定实体键、解析状态和归组决策；
2. 适配器测试：无 service、混合批次、commonLabels 合并、显式 service 兼容；
3. 迁移测试：升级、回填、空服务持久化、保护性降级和 ORM 一致性；
4. 服务测试：任务原子创建、重放、并发收敛、失败重试和解析后重新归组；
5. API 测试：无服务接入、列表详情和中文状态；
6. 前端测试：实体展示、待识别、无法识别、不适用和筛选；
7. 集成验证：使用真实 MySQL 和受控 Kubernetes 响应完成无服务告警接入与解析闭环；
8. 回归验证：现有统一后端脚本、前端测试、构建和禁止身份扫描全部通过。

## 11. 分阶段交付

1. 先完成可空服务、实体模型、无服务接入、实体归组和前端可见性；
2. 再完成持久解析任务和可选 Kubernetes Pod 解析器；
3. 最后执行真实集群只读联调，验证本地 KubePodNotReady 可被接收，并在 Pod 具有标准应用标签时解析到服务。

每一阶段都必须独立可用。第二、三阶段失败不能撤销第一阶段的告警接入能力。
