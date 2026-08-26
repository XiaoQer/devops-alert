# 宽容的 Alertmanager 告警接入

## 状态

已确认，实施中。

## 背景与目标

当前 Alertmanager 适配器把 `labels.service` 当成必填业务字段。实际 kube-prometheus-stack 内置告警经常只有 `namespace`、`pod`、`job`、`instance` 或 `node`。2026-08-26 对本地集群只读核验发现：131 条规则均未静态声明 `service`，当时 7 条 firing 告警也全部没有 `service`。

告警归集中心必须优先完整接收合法来源事实，不能要求所有来源遵守平台理想的业务标签规范。平台只约束协议、安全和容量边界；业务字段缺失时明确降级，不丢弃、不猜测、不访问 Kubernetes 补全。

## 范围

- Alertmanager 告警允许缺少 `service`、`environment`、`severity`、`summary`、`description` 和资源标签；
- `service` 在 SignalEvent、Alert 和 AlertGroup 中可空，不设置异步解析状态；
- 从已有标签确定性提取 SERVICE、WORKLOAD、POD、NODE、JOB、INSTANCE、CLUSTER 或 UNKNOWN 主要实体，仅用于展示和归组；
- 未知扩展字段忽略，已知字段继续执行长度、数量和禁止身份检查；
- 缺少服务的告警照常入库、查询、恢复、归组和展示；
- 告警组使用实体键、环境、症状和时间窗口归集；
- 服务为空的告警组不进入现有服务目录事故关联，保存可解释的跳过结果；
- 前端明确显示“服务未提供”和真实主要对象，不显示“正在识别”。

## 非目标

- 不访问 Kubernetes、Prometheus 或其他系统补全 service；
- 不新增解析 Worker、解析任务、Kubernetes 凭据或 RBAC；
- 不修改用户的 PrometheusRule 或 Alertmanager 配置；
- 不根据 Pod 名称、job、namespace、node 或 cluster 猜测业务服务；
- 不把 Incident 改造成任意实体事故；
- CloudEvents 和人工报告输入契约本阶段不变。

## 设计

完整设计见 `docs/superpowers/specs/2026-08-26-entity-aware-alert-intake-design.md`。

接入流程固定为：认证与容量检查 → Alertmanager v4 协议解析 → 白名单业务字段标准化 → 真实实体摘要 → 原子持久化。业务字段缺失只产生固定原因码和安全默认展示，不产生 HTTP 拒绝。

## 安全与恢复

- Webhook 仍限制 256 KiB 和单批最多 100 条；
- 标签和注解继续限制键值长度与数量；
- 禁止实验身份继续递归拒绝；
- 原始请求、未知字段、Generator URL 和 Secret 不持久化；
- 数据库迁移安全回填现有服务告警，降级时若存在空服务则明确停止；
- 无服务告警不会创建虚假服务目录项或虚假事故。

## 验收条件

- 不含 service 的单条、多条及混合 Alertmanager Webhook 返回 202；
- 缺少 environment 时保存 unknown，缺少或未知 severity 时保存 medium 并记录原因码；
- 缺少 summary 和 description 时仍可用 alertname 形成可展示标题与摘要；
- 未知扩展字段不会导致合法 Webhook 被拒绝，也不会进入持久化；
- Pod、Node、Job 等已有标签形成稳定实体摘要；没有资源标签时形成 UNKNOWN 实体；
- 相同无服务实体可进入同一告警组，不同实体不误合并；
- 无服务告警组不创建 Incident，并保存固定中文解释；
- 前端显示“服务未提供”和主要对象；
- 现有幂等、乱序、恢复、批次回滚、风暴收敛和安全测试保持通过；
- 后端统一验证、迁移往返、前端测试与构建全部通过。

## 验证证据

- AlertmanagerConfig 已取消 service 路由过滤，真实接收 6 条无 service 的 `KubePodNotReady` 告警；
- 无 service 归组领域测试和真实 MySQL 集成测试已通过；
- 本地历史失败归组任务重试后 6/6 成功，页面告警组总数从 2 增加到 8；
- 前端实体中文展示与无 service 事故关联跳过决策尚未完成，因此规格继续保持活跃。
