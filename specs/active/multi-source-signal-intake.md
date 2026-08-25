# 多源信号接入与告警投影

## 状态

已确认，实施中。领域模型、数据库迁移、纯领域 Alert 投影、共享接入服务、Alertmanager 纯适配器和安全 HTTP 入口已完成；CloudEvents 适配器和入口尚未实现。

## 背景与目标

后端阶段 1 已提供人工事故报告以及四类领域资源读取能力，但尚未接入真实监控来源。本规格增加 Alertmanager Webhook v4 和 CloudEvents HTTP 两个生产事件入口，将外部事件安全转换为不可变 SignalEvent，并依据稳定来源身份维护 Alert 当前状态投影。

本阶段严格停在 `SignalEvent → Alert`。外部告警不得直接创建 Incident 或 DiagnosisRun；事故候选过滤、服务目录和可解释关联由后续独立规格负责。

## 范围

- Alertmanager Webhook v4 结构校验、批量接入和字段转换；
- CloudEvents 1.0 结构化 JSON 与 HTTP Binary 接入；
- 人工报告、Alertmanager、CloudEvents 三套独立认证凭据；
- 共享信号接入服务、来源事件幂等和 Alert 稳定身份；
- 不含原始负载的外部信号幂等结果记录；
- firing、更新、resolved、迟到、重开和相同时间冲突规则；
- Alertmanager 最多 100 条、256 KiB；CloudEvents 单事件、64 KiB；
- 原子事务、有界审计、并发重放和数据库失败回滚；
- 领域模型与单一 MySQL 8.4 初始迁移，从空库建立完整当前结构。

## 非目标

- 根据外部告警直接创建或关闭 Incident；
- 创建 DiagnosisRun、执行自动取证或调用 AI；
- 服务目录、拓扑补充、事故候选过滤和事故关联；
- 保存完整 Webhook、任意 annotations、生成器 URL、查询、凭据或原始日志；
- Alertmanager 配置下发、Prometheus 规则管理或监控查询；
- 除 Alertmanager 和 CloudEvents 以外的外部适配器；
- 引入消息队列、Kafka 或异步原始事件落盘。

## 设计

完整设计见 `docs/superpowers/specs/2026-08-24-multi-source-signal-intake-design.md`。

核心结构：

`独立认证 → 严格适配器 → 通用 SignalCommand → 共享接入服务 → SignalEvent + Alert + 审计`

Alertmanager 使用 `fingerprint`，CloudEvents 使用受限 `data.alert_key` 作为来源内部稳定告警键。平台再以 `source + source_instance + source_alert_key` 形成全局唯一 Alert 投影身份。

每个不同的外部事实形成不可变 SignalEvent；完全重复事件幂等重放。Alert 是可更新投影，使用确定性事件时间规则处理触发、恢复、乱序和重开，并在有效更新时递增版本。

## 安全与恢复

- 人工报告、Alertmanager、CloudEvents Token 互不通用并使用恒定时间比较；
- 来源 URI 只参与不可逆来源摘要计算，不保存用户信息、查询参数或凭据；
- 适配前后递归拒绝实验身份、注入动作和标准答案；
- 所有请求在解析前受路径级容量限制，字段数量和字符串长度均有上界；
- Alertmanager 整批先验证再在一个事务中写入，一条失败则整批回滚；
- CloudEvents 单事件在一个事务中完成 SignalEvent、Alert 和审计；
- 数据库失败返回可重试安全错误，不输出原始请求或 Secret；
- 重复提交返回已有结果，不重复写入或追加审计；
- 迟到事件保留为 SignalEvent，但不得回退 Alert 当前状态。

## 验收条件

- Alertmanager 触发、内容更新、恢复、重开、重复和乱序行为符合固定规则；
- Alertmanager 批次超过 100 条或 256 KiB 时在持久化前拒绝；
- CloudEvents 结构化与 Binary 模式输出相同内部命令并支持 firing 与 resolved；
- CloudEvents 未支持类型、未知字段、非法时间或超过 64 KiB 时安全拒绝；
- 两种外部入口使用独立 Token，任何入口的 Token 不可访问另一入口；
- 不同来源实例的相同告警键不会碰撞；
- 并发重复事件只生成一份 SignalEvent，幂等响应指向相同记录；
- 一批中任一步失败后 SignalEvent、Alert 和审计均无部分残留；
- 原始负载、凭据、禁止实验身份、任意查询和生成器 URL 不进入数据库、响应或日志；
- 外部事件接入后 Incident 与 DiagnosisRun 记录数保持不变；
- 数据库迁移可从空库升级、降级，并包含人工报告和外部信号投影所需的完整结构；
- 所有自动状态决定都有固定原因码和有界审计证据。

## 验证证据

- 历史 PostgreSQL 两段迁移和回填验证已由无数据保留的 MySQL 基线取代；
- 当前已验证 `0001_mysql_initial → base` 往返、ORM 元数据一致性和完整投影结构；
- SignalEvent、Alert 和 `signal_intake_results` 的新增数据库约束及 ORM 元数据一致性已验证；
- 人工报告首次提交、重放、事务回滚与四类资源读取契约已回归；
- Alert 投影已验证 opened、updated、resolved、reopened、stale、orphan_resolved 六类结果，以及乱序、同时间恢复优先和人工抑制保护；
- 共享接入服务已验证单事务批次、六类投影结果、精确重放、内容冲突、禁止身份、强制并发竞争收敛、整批回滚、响应顺序和有界审计；
- 外部信号接入后 Incident 与 DiagnosisRun 保持为零，重放不追加审计；
- Alertmanager v4 纯适配器已验证官方结构映射、来源 URI 脱敏摘要、13 类严重度别名、默认原因码、恢复和未来时间、必填身份、容量、禁止身份与确定性事件 ID；
- 分组、接收器、JSON 键顺序、生成器 URL、来源查询和片段变化不会改变事件 ID，真实规范化内容变化会形成新事件 ID；
- Alertmanager HTTP 入口已验证三套 Token 隔离、精确路径 256 KiB 容量、100 条批次上限、单条和多条写入、更新、恢复、精确重放、整批拒绝、安全错误和零事故/零诊断；
- 当前 MySQL 8.4 全部后端测试为 179 项通过，覆盖率 96.51% 以上，Ruff、格式和 Mypy 均通过。

CloudEvents 适配器及入口和真实外部 HTTP 冒烟仍未实施，不得据此把完整规格标记为可用。
