# MySQL 8.4 持久化基线验收记录

## 状态

已通过。

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

## 当前运行环境冒烟

- 用户现有容器：`devops-assistant-mysql-1`；
- MySQL 版本：8.4.10；
- 端口：3307；
- 2026-08-25 只读检查确认 `incident_intelligence` 数据库不存在后，创建专用空库；
- Alembic 成功迁移到 `0001_mysql_initial (head)`；
- 七张业务表均使用 InnoDB，另有一张 Alembic 版本表；
- 临时启动 API 后，`GET /health/ready` 返回 HTTP 200、`status=ready` 和 `database=available`；
- 冒烟过程未写入事故业务数据，数据库凭据未进入源码、验收记录或 API 响应。

## 结论

MySQL 8.4 持久化规格的全部验收条件已满足。共享外部信号接入、Alertmanager 和 CloudEvents 仍属于后续规格，不计入本次可用能力。
