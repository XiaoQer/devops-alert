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

## 2026-08-24 后端阶段 1 已验收

已经实现并验证：

- Python/FastAPI 后端工程、依赖锁文件和统一验证脚本；
- 不依赖数据库的存活检查，以及检查 PostgreSQL 连接的就绪检查；
- SignalEvent、Alert、Incident、DiagnosisRun 四个不可变领域模型；
- 告警、事故运营和诊断任务三套独立状态枚举与迁移策略；
- 对实验身份、注入动作和标准答案的递归拒绝边界；
- PostgreSQL 16 初始迁移，包含四个领域表、幂等键表和追加式审计表；
- 来源身份唯一、状态取值、父子引用和字段容量等数据库约束；
- Alembic 升级、降级及 ORM 元数据一致性检查。
- 原子且幂等的人工事故报告业务服务；
- 单次人工报告会在一个事务内生成 SignalEvent、Alert、Incident、DiagnosisRun 和四条有界审计记录；
- 相同幂等键与相同内容安全重放，不同内容返回稳定冲突；并发重复请求只生成一套领域记录；
- 任一步失败会回滚全部写入，实验身份在持久化前被拒绝，审计详情不保存原始事故描述或请求负载。
- `POST /api/v1/manual-reports` 人工事故报告接口；
- Bearer Token 使用恒定时间比较，幂等键、请求字段、标签数量、未来时间和 64 KiB 请求体均有明确边界；
- 首次创建返回 201，相同内容重放返回 200，不同内容冲突返回 409；认证、容量、校验和数据库失败使用稳定中文错误契约；
- 声明长度和无声明长度的分块请求都会执行请求体上限，错误响应不回显 Token、原始请求体或被拒绝值。
- SignalEvent、Alert、Incident、DiagnosisRun 四类资源的独立读取 API；
- 四类读取接口使用同一认证边界，格式错误和不存在统一返回 `resource_not_found`，不泄露授权范围外信息；
- 每次读取只查询对应的一张领域表，不加载幂等记录、指纹、审计详情或其他领域对象正文；
- 读取响应仅包含明确白名单中的规范化字段、关联 ID、状态、时间和版本。

Compose 独立环境中的最终验证结果为 91 项测试通过，覆盖率 95.21%，Ruff、格式检查、Mypy、依赖一致性和 PostgreSQL 集成测试通过。真实 HTTP 冒烟验证了首次创建 201、相同内容重放 200、四个 ID 一致，以及四类资源读取 4/4 成功。

## 2026-08-24 后端阶段 2 实施中

已经实现并验证：

- SignalEvent 增加版本化事件类型，Alert 增加来源、来源实例摘要、稳定告警键和状态变更时间；
- PostgreSQL `0002_multi_source_signal_intake` 迁移，可安全回填现有人工报告数据并可完整降级；
- 新增 `signal_intake_results` 幂等结果表骨架，只允许保存规范化 ID、固定结果码和时间，不保存原始请求；
- 人工报告已适配新模型，幂等键只生成不可逆告警键摘要，不进入 Alert 读取响应的原始字段；
- 资源读取 API 显式展示事件类型与 Alert 规范化来源身份，同时继续排除 source_event_id、payload_fingerprint 和幂等记录；
- 已实现纯领域 Alert 投影决策，不访问数据库、时钟、UUID 或 HTTP；
- 投影规则覆盖首次触发、同轮更新、恢复、乱序忽略、新轮重开、孤立恢复忽略、同时间恢复优先和人工抑制保护；
- SignalCommand 使用不可变有界契约，并校验来源类型、摘要身份、事实数量、原因码和事件时间顺序；
- 统一验证为 107 项测试通过，覆盖率 96.83%，Ruff、格式检查、Mypy、迁移往返和 PostgreSQL 集成测试通过。

尚未实现：共享外部信号接入服务、Alertmanager 与 CloudEvents 适配器及其 HTTP 入口。因此当前仍不能接收真实外部告警。

仍未实现：

- Alertmanager、CloudEvents 和其他外部适配器；
- 服务目录、事故关联、自动取证、Worker 注册和 AI 分析；
- 事故运营写接口、恢复闭环和前端；
- 生产部署制品、Kubernetes Chart 和真实监控或 AI 提供方接入。

## 下一步门槛

1. 实现原子、幂等、可审计的共享外部信号接入服务；
2. 共享服务验证通过后，再分别接入 Alertmanager 与 CloudEvents；
3. 完成多源接入后再设计轻量服务目录和可解释事故关联；
4. 在上述能力实现前，不得把外部接入、关联、自动取证、Worker 或 AI 标记为可用。

## 2026-08-25 MySQL 基线切换实施中

已经完成并通过聚焦验证：

- 生产依赖由 psycopg 切换为 PyMySQL；
- Settings 和 engine 工厂只接受包含数据库名的 `mysql+pymysql` URL；
- engine 固定 READ COMMITTED、连接池预检查、utf8mb4 和 UTC 会话时区配置；
- UTC DATETIME(6) 类型严格执行感知时间写入、UTC 规范化、感知时间读取和微秒保留；
- ORM 已使用 MySQL JSON，并声明 InnoDB、utf8mb4 和大小写敏感表排序规则；
- 52 项 UTC 与纯领域测试通过，Ruff、格式和 Mypy 通过。

当前处于有意的中间状态：Alembic 迁移和测试数据库仍是 PostgreSQL 基线，因此数据库持久化能力暂时不能作为可用 MySQL 能力验收。下一步必须建立独立 MySQL 测试环境并重建初始迁移。
