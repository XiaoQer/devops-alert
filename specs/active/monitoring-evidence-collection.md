# Incident 监控数据自动取证

## 状态

正式规格已确认，实施计划已完成，等待分阶段开发与验收。

## 背景与目标

平台已经能够从真实 Alert 创建正式 Incident，但当前 Incident 只展示触发告警和人工处置记录，不能自动保存故障发生时的监控证据。本规格建立异步自动取证基线：使用 Prometheus、ELK 和 SkyWalking 的版本化只读查询，把指标、日志、链路和确定性跨源关联转换成标准证据，并在 Incident 中集中展示。

完整设计见 `docs/superpowers/specs/2026-09-02-monitoring-evidence-collection-design.md`，实施计划见 `docs/superpowers/plans/2026-09-02-monitoring-evidence-collection.md`。

## 范围

- 测试环境一套 Prometheus、Elasticsearch 和 SkyWalking 数据源；
- Incident 创建后自动取证与人工重新取证；
- 基线窗口和故障窗口对照；
- 通用、HTTP、JVM、MySQL 版本化取证包；
- 持久化 EvidenceRun、EvidenceItem 和异步任务；
- 三类只读适配器与有界标准化结果；
- 确定性跨源关联；
- Incident 取证 API、健康摘要和第三版页面结构。

## 非目标

- AI、RAG、根因结论、自动修复或处置建议；
- AI 或用户生成监控查询；
- 多集群、多套同类型数据源或跨环境取证；
- Kubernetes、故障注入平台、发布系统和配置中心；
- 保存无限原始监控数据；
- 用户自定义取证包。

## 设计

- Incident 创建事务写入一条自动 EvidenceRun 和持久化取证任务；
- Worker 根据 Incident 与 Alert 确定 environment、service、Alertname 和锚点；
- `common-service` 必选，HTTP/JVM/MySQL 包按 Alertname 和结构化标签追加；
- 基线窗口为锚点前 30 至前 10 分钟，故障窗口为锚点前 10 分钟至运行开始，最多查询两小时；
- 三个适配器独立失败，成功结果继续保存；
- 历史运行不可覆盖，人工重新取证创建新运行；
- Incident 同时最多一个运行中任务；
- 页面以关键发现为主线，证据向下展开，右侧展示来源状态、时间窗口和规则包。

## 安全与恢复

- Secret 只从运行环境读取，不进入数据库、源码、日志、API、测试数据或证据；
- 适配器只执行版本化预定义只读查询；
- 日志字段白名单、敏感模式脱敏，指标、日志和 Trace 全部有硬上限；
- Worker 使用租约、幂等和有界重试；
- 监控系统不可用不阻塞告警接收、Incident 创建和人工处置；
- 不读取或保存故障注入身份与内部状态。

## 验收条件

- 新建 Incident 同事务产生且只产生一个自动取证任务；
- 人工重新取证幂等，历史运行不被覆盖；
- 同一 Incident 不会并发运行两个取证任务；
- service 缺失时不猜测，不依赖 service 的证据仍可执行；
- 基线和故障窗口计算准确且不超过两小时；
- Prometheus、ELK、SkyWalking 只执行允许的模板并输出标准 EvidenceItem；
- 单数据源失败形成部分成功，不阻断其他证据或 Incident；
- 日志、指标和 Trace 的条数、字段、长度和敏感信息受控；
- 跨源关联只描述可证明事实，不输出根因或置信度；
- 数据源配置按环境隔离，每环境每类型最多一个启用来源；
- Incident 页面展示历史运行、关键发现、证据展开、来源状态和查询依据；
- 统一后端验证、前端测试、生产构建和真实浏览器主流程全部通过。

## 验证证据

尚未实施。
