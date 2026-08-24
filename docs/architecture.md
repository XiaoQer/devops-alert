# 架构意图

> 状态：设计已确认，后端阶段 1 实施中。

## 项目边界

本仓库是独立的生产事故智能平台。故障注入平台位于其他仓库，拥有独立 Git 历史、构建、数据库、权限和部署。两个平台不共享源码模块、数据库或运行时开关，只能通过正式版本化 API、Webhook 和事件契约联调。

非生产故障实验制造真实业务异常后，监控系统像生产环境一样独立产生信号。本平台不知道注入动作。报告封存后，故障平台一侧的隔离评测器可以通过只读公开接口评分，但不得回写事故事实或 AI 报告。

## 默认部署

一个统一 UI 与一个逻辑核心控制器构成默认安装。核心控制器内置：

- Signal Ingestion Gateway；
- Adapter Registry；
- Signal/Alert Store；
- Service Catalog；
- Correlation Engine；
- Incident Manager；
- Diagnosis Orchestrator；
- Capability Resolver。

按需注册的独立 Worker 包括：

- Diagnosis Worker；
- Analysis Worker；
- 后续 Notification/Collaboration Worker；
- 后续 Correlation Learning Worker。

注册只声明能力、依赖和健康状态；异步任务使用持久任务、租约和过期接管，不通过注册回调传递业务任务。

## 领域边界

- `SignalEvent`：外部系统发生的一次不可变事实；
- `Alert`：相同来源条件的去重和当前状态投影；
- `Incident`：需要协调、调查、缓解或恢复验证的运营对象；
- `DiagnosisRun`：事故某一上下文版本的一次自动诊断尝试。

三套状态机独立：

- 告警：`ACTIVE → RESOLVED`，或 `SUPPRESSED`；
- 事故：`DETECTED → TRIAGING → INVESTIGATING → MITIGATING → MONITORING_RECOVERY → RESOLVED → CLOSED`；
- 诊断：`QUEUED → COLLECTING → NORMALIZING → SNAPSHOT_READY → ANALYZING → REPORT_READY`，并有部分、失败和人工复核分支。

## 主数据流

1. 来源适配器认证、校验并输出统一 SignalEvent；
2. 平台按来源稳定身份幂等保存事件；
3. 适配器去重键把触发、更新和恢复聚合为 Alert；
4. 服务目录补充实体、环境、所有者和一跳依赖；
5. 事故候选规则过滤维护窗口、噪声和不可行动告警；
6. 关联引擎依据精确内容、实体、时间、拓扑、症状和变更选择已有事故或创建新事故；
7. 诊断编排器组合通用基础包、实体包、症状增强包和受控服务扩展；
8. Diagnosis Worker 执行版本化预定义只读查询并生成确定性中文结果；
9. 系统封存不可变证据快照；
10. Analysis Worker 生成报告并通过 Schema、证据引用和事实一致性校验；
11. 操作员认领、调查、缓解、恢复验证、解决和关闭事故；
12. 新信号或延迟证据只产生新版本，不覆盖历史事件、快照和报告。

## 关联策略

首版只使用可审核的确定性规则：

- 相同稳定关联键；
- 相同实体或服务目录一跳依赖；
- 默认 15 分钟、限制在 5–60 分钟的滚动时间窗口；
- 已审核症状因果或共同根因规则；
- 同一实体邻近部署、配置和 Feature Flag 变更；
- 经审核发布的人工合并或拆分模式。

每次自动关联必须保存规则版本、事实、固定原因码和中文解释。高可信规则才能自动合并；中低可信候选创建独立事故并提示可能相关。关联引擎异常时安全退化为独立事故。

## 诊断策略

每次 DiagnosisRun 组合：

1. 通用基础包；
2. Kubernetes、JVM、Node.js、MySQL、外部 HTTP 等实体类型包；
3. CPU、内存/OOM、延迟/错误率、Pod 重启、数据库连接/锁等待等症状增强包；
4. 通过 Schema、安全审核和回放测试的服务专用扩展。

每项排查必须预先声明查询、参数、超时、容量、脱敏、结果转换、证据角色和失败语义。AI 只接收封存后的事故事实、拓扑摘要、排查结果和代表性证据。

## 技术约束

- 后端统一采用 Python，HTTP API 使用 FastAPI，输入输出模型使用 Pydantic；
- 数据访问使用 SQLAlchemy，数据库迁移使用 Alembic，主数据库使用 PostgreSQL；
- 前端采用 Vue 3、TypeScript 和 Vite，页面状态使用 Pinia；
- 后端发布 OpenAPI 契约，前端从契约生成接口类型，不手工维护重复 DTO；
- Diagnosis Worker 和 Analysis Worker 继续采用 Python，通过版本化任务契约按能力注册，不直接依赖前端；
- 首期异步任务使用 PostgreSQL 持久任务、租约、心跳和超时接管，暂不引入 Kafka；
- 后端测试使用 Pytest，前端单元测试使用 Vitest，关键业务旅程使用 Playwright；
- 前后端形成独立构建制品，默认作为一套平台部署；可选 Worker 独立部署和扩缩容；
- 首版不使用 Kafka、图数据库或完整 Backstage；
- 外部事件契约参考 CloudEvents 1.0；
- 服务与资源身份优先采用 OpenTelemetry Resource/Semantic Conventions；
- 具体运行时版本、依赖版本、目录结构和本地启动方式由阶段 1 实施计划锁定。
