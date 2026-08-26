# 问题签名告警归集

## 状态

已完成并验收。

## 背景

当前归组把实体键作为相似性主键。service 缺失时实体会退化为 Pod、Node 或 Instance，导致同一批 `KubePodNotReady` 因 Pod 不同形成多个告警组。资源实例是影响对象，不等于问题边界。

## 目标

使用告警来源、问题类型、标准化症状、环境和影响范围生成版本化问题签名，让同一范围内的相似告警可靠收敛，同时保留每个资源的独立事实和审计。

## 范围

- 新增 `problem-signature.v1` 纯领域推导；
- Alertmanager 白名单事实保存 `alertname` 和 `workload`；
- AlertGroup 持久化问题签名和影响范围摘要；
- 归组候选从实体键切换到问题键；
- unknown 症状在问题类型和范围确定时允许归组；
- 有界、可审计地重新归组现有无服务、无事故活动组；
- 前端显示问题类型、范围、真实资源数和“服务未提供”；
- 无服务组保存事故关联跳过决策。

## 非目标

- 不查询 Kubernetes 或 Prometheus 补全标签；
- 不根据 Pod 名称猜测 Deployment、服务或所有者；
- 不跨告警源自动归组；
- 不自动重写已关联事故或已解决历史组；
- 不引入机器学习、向量检索或 AI 参与实时归组；
- 不改变 Alertmanager 的原始告警去重身份。

## 规则

- SERVICE、WORKLOAD、NAMESPACE、CLUSTER、JOB 范围使用 300 秒窗口；
- SOURCE 安全退化范围使用 120 秒窗口；
- 唯一候选自动加入，多个候选独立建组并记录歧义；
- Pod、Node、Instance 和 Container 仅作为影响资源，不作为问题主键；
- service 为空时不得创建虚假服务或 Incident；
- 所有摘要有界，原始载荷、未知标签和 Secret 不持久化。

## 验收条件

- 6 条同来源、同 Namespace、不同 Pod 的 `KubePodNotReady` 形成 1 个 ACTIVE 告警组；
- 该组显示 6 条原始告警、6 个影响资源和正确 Namespace 范围；
- 不同 Namespace、alertname、环境或告警源分别形成不同组；
- 同 service 的既有错误率或延迟风暴继续正确收敛；
- 问题类型明确但 symptom=unknown 的告警可以归组；
- 无 service 告警组产生 `SKIPPED_SERVICE_MISSING`，不创建 Incident；
- 旧的 6 个无服务活动组可有界重组，已关联事故和已解决组保持不变；
- API 和前端显示“服务未提供”、问题类型、范围及真实资源数；
- 后端全部测试、迁移往返、Ruff、格式、Mypy、前端测试和构建通过。

## 设计

完整设计见 `docs/superpowers/specs/2026-08-26-problem-signature-alert-grouping-design.md`。

## 验证证据

- 后端统一验证：487 项测试通过，覆盖率 91.89%，Ruff、格式检查及 93 个源码文件 Mypy 检查通过；
- 前端统一验证：66 项测试和 Vite 生产构建通过；
- 容量验证：1,000 个不同 Pod、相同稳定问题类型收敛为 1 个告警组；
- 真实 MySQL 已升级到 `0008_problem_signature_grouping`；有界重组检查 6 个旧组、合并 5 个并迁移 5 个成员；
- 真实页面显示 1 个包含 6 条原始告警、6 个影响资源的 Namespace 级问题组，明确展示“服务未提供”和不创建事故的原因；
- 已验证 Alertmanager 容器可通过 `host.docker.internal:8000` 访问后端健康检查；
- 浏览器控制台无错误或警告。
