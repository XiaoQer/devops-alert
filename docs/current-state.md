# 当前状态

## 2026-08-24 项目初始化

- 独立项目目录和 Git 仓库已建立。
- 产品边界、目标架构和第一份活跃规格已记录。
- 已确认 Python/FastAPI、Pydantic、SQLAlchemy/Alembic、PostgreSQL 与 Vue 3/TypeScript/Vite/Pinia 技术基线。
- 已确认首期使用 PostgreSQL 持久任务和租约，暂不引入 Kafka。
- 初始化时没有后端、前端、数据库、迁移、容器、Kubernetes Chart、连接器、Worker 或测试实现。
- 当前没有从旧故障注入项目复制代码、数据或 Git 历史。
- 当前没有接入 Prometheus、Alertmanager、OpenTelemetry、日志、Trace、Kubernetes 或 AI 提供方。
- 初始化时所有产品能力均处于计划状态。

## 2026-08-24 后端阶段 1 实施中

已经实现并验证：

- Python/FastAPI 后端工程、依赖锁文件和统一验证脚本；
- 不依赖数据库的存活检查，以及检查 PostgreSQL 连接的就绪检查；
- SignalEvent、Alert、Incident、DiagnosisRun 四个不可变领域模型；
- 告警、事故运营和诊断任务三套独立状态枚举与迁移策略；
- 对实验身份、注入动作和标准答案的递归拒绝边界；
- PostgreSQL 16 初始迁移，包含四个领域表、幂等键表和追加式审计表；
- 来源身份唯一、状态取值、父子引用和字段容量等数据库约束；
- Alembic 升级、降级及 ORM 元数据一致性检查。

本批次验证结果为 46 项测试通过，覆盖率 97.81%，Ruff、格式检查、Mypy、依赖一致性和 PostgreSQL 集成测试通过。

仍未实现：

- 人工事故报告业务服务及 HTTP API；
- Alertmanager、CloudEvents 和其他外部适配器；
- 服务目录、事故关联、自动取证、Worker 注册和 AI 分析；
- 事故运营写接口、恢复闭环和前端；
- 生产部署制品、Kubernetes Chart 和真实监控或 AI 提供方接入。

## 下一步门槛

1. 实现原子且幂等的人工事故报告服务；
2. 实现认证、有界输入和稳定错误契约的人工报告 API；
3. 实现四类领域资源读取 API；
4. 完成阶段 1 的全量验证、运行说明和验收记录。
