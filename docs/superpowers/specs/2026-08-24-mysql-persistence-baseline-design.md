# MySQL 8.4 持久化基线设计

## 1. 背景与决策

Incident Intelligence 当前仍处于开发阶段，PostgreSQL 中没有需要保留的数据。项目决定停止支持 PostgreSQL，把 MySQL 8.4 作为唯一主数据库，并从空库重建迁移基线。

本次调整先于共享信号接入服务实施。数据库基线切换完成并恢复全部现有能力后，再继续实现 SignalEvent → Alert 的原子接入、Alertmanager 和 CloudEvents，避免数据库迁移与新业务链路同时变化。

## 2. 目标

- 后端只支持 MySQL 8.4，不保留 PostgreSQL 或 SQLite 运行兼容；
- 保持 FastAPI、Pydantic、SQLAlchemy 和 Alembic 技术边界；
- 从空 MySQL 数据库建立与当前领域模型一致的完整结构；
- 保持人工报告、资源读取、幂等、事务回滚和安全错误契约；
- 为后续共享信号接入提供可靠的事务、行锁、唯一约束和并发重试基础；
- 日常开发数据库与自动化测试数据库严格隔离。

## 3. 非目标

- 迁移或保留现有 PostgreSQL 数据；
- 同时兼容 PostgreSQL、MySQL、MariaDB 或 SQLite；
- 提供 PostgreSQL → MySQL 数据搬迁工具；
- 改变 SignalEvent、Alert、Incident、DiagnosisRun 的业务含义；
- 在本次切换中实现共享信号接入、Alertmanager 或 CloudEvents；
- 引入存储过程、触发器、分布式锁或消息队列。

## 4. 技术基线

- 数据库：MySQL 8.4；
- 存储引擎：InnoDB；
- 字符集：utf8mb4；
- Python 驱动：PyMySQL；
- SQLAlchemy URL：`mysql+pymysql://...`；
- 迁移：Alembic；
- 事务隔离：READ COMMITTED；
- JSON：MySQL 原生 JSON；
- 时间：UTC `DATETIME(6)`。

项目删除 psycopg、PostgreSQL JSONB 和 PostgreSQL 专用连接检查。运行配置只接受 MySQL URL。

## 5. 迁移策略

由于没有需要保留的数据，删除尚未发布的 PostgreSQL `0001_initial_domain` 和 `0002_multi_source_signal_intake`，创建新的 `0001_mysql_initial`。

新迁移一次创建当前完整结构：

- `signal_events`；
- `alerts`；
- `incidents`；
- `diagnosis_runs`；
- `ingestion_keys`；
- `signal_intake_results`；
- `audit_events`。

所有表使用 InnoDB。迁移必须支持从空库 `upgrade head` 和 `downgrade base`，并通过 Alembic ORM 元数据一致性检查。

身份、来源事件、摘要和幂等字段必须使用大小写敏感比较，防止外部身份因 MySQL 默认不区分大小写的排序规则而错误合并。中文标题、摘要和有界事实使用 utf8mb4。

状态、环境、严重度、事件类型、投影结果、版本、字段长度、外键和唯一身份继续由数据库约束保护。MySQL 8.4 是唯一受支持版本，不为旧版 MySQL 或 MariaDB 的 CHECK 行为提供降级分支。

## 6. UTC 时间类型

MySQL `DATETIME` 不保存时区。项目增加 SQLAlchemy UTC 时间类型：

- 写入时要求 Python 值包含时区；
- 写入前转换为 UTC 并去除 `tzinfo`；
- 数据库存储 `DATETIME(6)`；
- 读取后恢复为 `tzinfo=UTC` 的感知时间；
- 拒绝无时区时间，避免把本地时间静默当成 UTC；
- 保留微秒精度。

迁移中的所有领域时间列与 ORM 必须使用一致的 `DATETIME(6)` 语义。领域模型继续只接受 UTC 可感知时间。

## 7. 本地运行与测试隔离

日常运行连接用户现有的 MySQL 8.4：本地映射端口为 3307。数据库名、账号和密码只通过 `II_DATABASE_URL` 提供，不写入源码、样例密钥、日志、响应或测试数据。

项目保留 `compose.yaml`，但其数据库服务改为独立 MySQL 8.4 测试实例。自动化测试使用 `II_TEST_DATABASE_URL`，该 URL 必须选择 MySQL 自带的 `mysql` 引导库，不得选择项目日常数据库。测试凭据通过当前终端环境注入 Compose，不写入仓库。

测试生命周期：

1. 校验 URL 驱动和后端均为 MySQL，且初始数据库名严格等于 `mysql`；
2. 连接测试专用 MySQL 实例；
3. 创建 `ii_test_<随机标识>` 临时数据库；
4. 在该数据库执行迁移和测试；
5. 清理前再次校验名称前缀和本轮随机标识；
6. 只删除本轮临时数据库。

测试不得接受 PostgreSQL、SQLite 或无法识别的数据库地址。Compose 使用独立容器、端口和测试账号，不访问用户现有的 3307 数据库。

## 8. 事务、幂等与并发

后续共享信号接入服务在一个 InnoDB 事务内完成：

1. 写入不可变 SignalEvent；
2. 应用纯领域 Alert 投影规则；
3. 创建或更新 Alert；
4. 保存首次幂等结果；
5. 追加有界审计；
6. 整批一次提交。

任一步失败必须整批回滚。已存在 Alert 使用 `SELECT ... FOR UPDATE` 串行化更新；尚不存在 Alert 时依靠 `(source, source_instance, source_alert_key)` 唯一约束竞争创建。唯一约束竞争失败后，失败事务必须完整回滚，并在新的工作单元中重试一次。

MySQL 会话事务隔离固定为 READ COMMITTED，减少默认 REPEATABLE READ 的范围锁和间隙锁影响。不使用 `GET_LOCK()`、存储过程或触发器承载业务状态。

幂等语义不因数据库切换而改变：

- 同一来源事件和相同内容返回首次结果；
- 同一来源事件身份和不同内容返回 `source_event_conflict`；
- 重放不新增 SignalEvent、Alert 版本或审计；
- Alertmanager 批次任意一条失败则整批零写入；
- 孤立 resolved 的首次 `alert_id = NULL` 在后续重放时仍为 NULL；
- 外部信号接入不得创建 Incident 或 DiagnosisRun。

## 9. 安全与错误处理

- Secret 不得进入源码、镜像、日志、API 响应、测试数据或事故快照；
- 数据库不可用继续映射为稳定的 `persistence_unavailable`；
- 错误响应不得回显数据库 URL、主机、端口、数据库名、用户名或密码；
- 审计只保存资源 ID、来源类型和固定原因码；
- 审计不保存标题、摘要、标签、URI、Token 或原始请求；
- 数据库测试清理只允许删除经过精确校验的本轮临时数据库。

## 10. 验收条件

- 空 MySQL 8.4 可执行 `upgrade head → downgrade base`；
- Alembic 与 SQLAlchemy ORM 元数据一致；
- 所有表为 InnoDB，JSON 列使用 MySQL JSON；
- 外键、CHECK、长度、状态、唯一身份和大小写敏感规则通过真实 MySQL 测试；
- UTC 感知时间往返后时间和微秒保持一致，无时区时间被拒绝；
- 人工报告创建、重放、冲突、并发重复和失败回滚行为不变；
- 四类资源读取 API 契约不变；
- 测试拒绝 PostgreSQL、SQLite 和日常数据库；
- PostgreSQL 驱动、JSONB、Compose 服务和当前架构说明全部移除或标记为已替代；
- Ruff、格式、Mypy、全部自动化测试和覆盖率门槛通过；
- 使用用户现有 MySQL 时只通过环境变量接收凭据，并完成空项目库迁移验证。

## 11. 文档处理

产品、架构、当前状态、活跃规格、架构决策和当前实施计划统一改为 MySQL 8.4。

历史验收文档保留 PostgreSQL 当时的验证事实，但明确注明该基线已被 MySQL 设计替代。不得把历史验证结果继续描述为当前运行能力。

## 12. 实施顺序

1. 更新规格、架构决策和依赖基线；
2. 实现 UTC 时间类型和 MySQL ORM；
3. 重建 MySQL 初始迁移；
4. 改造测试数据库生命周期与 Compose；
5. 恢复人工报告、资源读取、迁移和约束测试；
6. 在项目独立 MySQL 上执行完整验收；
7. 在用户现有 MySQL 上执行受控空库迁移验证；
8. 完成后再恢复共享信号接入服务开发。
