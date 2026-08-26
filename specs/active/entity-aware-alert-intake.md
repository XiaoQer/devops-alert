# 面向实体的告警接入与服务识别

## 状态

已确认，待实施。

## 背景与目标

当前 Alertmanager 适配器要求每条告警都包含 `labels.service`。实际的 kube-prometheus-stack 内置规则通常只携带 `namespace`、`pod`、`job`、`instance` 或 `node` 等资源身份。2026-08-26 对本地 `docker-desktop` 集群的只读核验显示：131 条告警规则没有任何一条静态声明 `service`；当时 7 条 firing 告警也全部没有 `service`。

平台必须先可靠接收外部事实，再独立解析受影响实体和业务服务。缺少 `service` 不得导致整批告警被拒绝，不得伪造服务名，也不得阻断告警中心的人工处置。

## 范围

- Alertmanager 接入允许每条告警缺少 `labels.service`；
- SignalEvent、Alert 和 AlertGroup 支持可空服务及明确的主要实体身份；
- 从告警标签确定性提取 SERVICE、WORKLOAD、POD、NODE、JOB、INSTANCE、CLUSTER 或 UNKNOWN 实体；
- 保留服务识别状态、来源、可信度和固定原因码；
- 未识别服务的告警仍进入告警中心，并按实体、环境、症状和时间窗口归组；
- 未识别服务的告警组不进入现有服务型事故自动合并，保存可解释的跳过结果；
- 服务识别成功后更新 Alert 当前投影，创建新的归组任务，并通过现有持久任务链重新参与事故关联；
- 增加可选的 Kubernetes 只读实体解析器，通过 `namespace + pod` 读取 Pod 标准标签，优先识别 `app.kubernetes.io/name`，其次识别 `app`；
- 前端告警列表、告警详情和告警组显示主要实体、服务识别状态及中文原因。

## 非目标

- 不修改用户集群中的 PrometheusRule、Alertmanager 配置或 Kubernetes 资源；
- 不根据 Pod 名称、Deployment 名称字符串或正则表达式猜测服务；
- 不把 `job`、`namespace`、`node` 或 `cluster` 直接冒充业务服务；
- 不在接入事务中同步访问 Kubernetes；
- 不让 AI 直接访问 Kubernetes 或生成查询；
- 本阶段不把 Incident 全面改造成任意实体事故，现有自动事故关联仍以已识别服务为门槛；
- 不实现跨集群资源图、自动服务目录同步或多跳所有者解析。

## 设计

完整设计见 `docs/superpowers/specs/2026-08-26-entity-aware-alert-intake-design.md`。

### 核心原则

1. 接入完整性优先：协议合法的告警不因缺少服务标签而丢失；
2. 原始事实与当前投影分离：SignalEvent 不可变，Alert 可以保存后续解析出的服务；
3. 实体不是服务：资源身份和业务服务分别建模；
4. 解析可追溯：每次解析保存来源、可信度、原因码和时间；
5. 安全退化：解析器、Kubernetes 或服务目录不可用时保持待识别，不阻断告警运营；
6. 不猜测：只有显式标签或受信只读数据源能确认服务。

## 安全与恢复

- Kubernetes 解析器只需要读取 Pod 元数据，不需要写权限；
- 解析任务有界、持久、可重放并使用租约，失败采用固定错误码；
- API 不返回 Kubernetes Token、kubeconfig、原始请求或不受限标签；
- 关闭解析器不会影响 Alertmanager 接入和告警读取；
- 数据库迁移必须可从现有数据安全回填并可完整降级；
- 旧告警回填为 SERVICE 实体时仅使用已经持久化的真实 `service`，不重新解释历史原始负载。

## 验收条件

- 不含 `service` 的单条及多条 Alertmanager Webhook 返回 202，并分别生成 SignalEvent 与 Alert；
- 同批一条有服务、一条无服务时整批成功，不发生部分写入或整批拒绝；
- 显式 `service` 保持现有告警身份、归组和事故关联行为；
- 无服务的 Pod 告警保存 `entity_type=POD`、稳定实体键和 `service_resolution_status=PENDING`；
- 无服务的 Node 或 Cluster 告警按真实实体归组，不创建虚假的服务或事故；
- 未识别服务的告警组产生固定的 `SKIPPED_UNRESOLVED_SERVICE` 可解释结果；
- Kubernetes 解析器从 Pod 的 `app.kubernetes.io/name` 解析服务后，Alert 投影更新并重新进入归组链路；
- Pod 不存在、权限不足、超时、标签缺失和解析器关闭都有稳定状态，不阻断接入；
- 告警中心用中文展示“已识别服务”“正在识别”“无法识别”以及依据；
- 现有 Alertmanager、CloudEvents、人工报告、告警风暴归组、事故关联和事故处置测试保持通过；
- 统一验证脚本、迁移往返、Ruff、格式、Mypy、前端测试和构建全部通过。

## 验证证据

待实施后补充。
