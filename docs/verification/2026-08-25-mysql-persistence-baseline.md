# MySQL 8.4 持久化基线验收记录

## 状态

项目独立 MySQL 验证已通过；用户现有 MySQL 的受控空库迁移与健康检查待完成。

## 验收范围

- PyMySQL、SQLAlchemy、Alembic 与 MySQL-only URL 边界；
- InnoDB、utf8mb4、`utf8mb4_bin`、READ COMMITTED 和 UTC 会话；
- 单一 `0001_mysql_initial` 的升级、ORM 一致性和降级；
- MySQL JSON、UTC `DATETIME(6)` 微秒往返和大小写敏感身份；
- 人工报告事务、幂等重放、并发收敛、失败回滚和四类资源读取；
- Secret、测试数据库删除保护和隔离测试环境自动清理。

## 自动化验证

2026-08-25 在项目 Compose 的独立 MySQL 8.4 环境执行 `./scripts/verify-backend.sh`：

- Ruff：通过；
- 格式检查：51 个文件已格式化；
- Mypy：33 个源文件无问题；
- Pytest：133 项通过；
- 覆盖率：96.04%，高于 90% 门槛；
- 测试完成后容器、网络和测试卷已自动删除。

聚焦验证另包含：

- 6 项迁移、中文 JSON、UTC 微秒和来源身份测试通过；
- 57 项持久化、人工报告和资源读取回归通过；
- 完全相同来源与告警身份被唯一约束拒绝，仅大小写不同的来源身份可共存；
- MySQL CHECK 错误码 3819 由当前 PyMySQL 分类为 OperationalError，约束本身正常生效；
- 业务仓储和事务代码不需要 PostgreSQL/MySQL 双方言分支。

## 当前运行环境预检

- 用户现有容器：`devops-assistant-mysql-1`；
- MySQL 版本：8.4.10；
- 端口：3307；
- 2026-08-25 只读检查确认 `incident_intelligence` 数据库不存在；
- 尚未创建数据库、执行迁移或启动 API 健康检查；
- 当前终端尚未提供 `II_MYSQL_BOOTSTRAP_URL`，因此未读取或记录任何数据库凭据。

## 剩余验收

用户在当前终端提供只选择 MySQL 自带 `mysql` 库的引导 URL 后，需创建专用空库、执行迁移到 `0001_mysql_initial`、确认七张业务表，并验证 `/health/ready` 返回数据库可用。完成前，本规格保持“实施中”。
