# MySQL 8.4 持久化基线

## 状态

已确认，实施中。MySQL-only 连接、UTC/ORM、隔离 Compose 测试数据库和单一初始迁移已完成；完整业务回归和最终验收尚未完成。

## 背景与目标

项目仍处于开发阶段，PostgreSQL 中没有需要保留的数据。主数据库改为 MySQL 8.4，并从空库重建迁移基线，为人工事故报告、资源读取和后续多源信号接入提供唯一、可重复验证的持久化实现。

## 范围

- MySQL 8.4、InnoDB、utf8mb4 和 READ COMMITTED 运行基线；
- PyMySQL、SQLAlchemy、Alembic 与 MySQL-only URL 校验；
- MySQL JSON、大小写敏感身份与 UTC `DATETIME(6)`；
- 删除 PostgreSQL 迁移链并创建单一 `0001_mysql_initial`；
- 项目独立 MySQL Compose 测试实例和随机临时测试数据库；
- 人工报告、资源读取、事务、幂等、约束和安全错误回归；
- 产品、架构、决策、当前状态、运行和验证文档更新。

## 非目标

- 保留或迁移 PostgreSQL 数据；
- 同时支持 PostgreSQL、MariaDB 或 SQLite；
- 实现共享信号接入服务、Alertmanager 或 CloudEvents；
- 引入存储过程、触发器、分布式锁或消息队列；
- 修改四个领域对象和三套状态机的业务含义。

## 设计

完整设计见 `docs/superpowers/specs/2026-08-24-mysql-persistence-baseline-design.md`。

项目删除 psycopg、PostgreSQL JSONB 和旧迁移，以当前完整 ORM 模型生成一份 MySQL 初始迁移。UTC 时间由 SQLAlchemy 自定义类型在 Python 感知时间与 MySQL 无时区 `DATETIME(6)` 之间严格转换。日常运行连接用户现有的 MySQL 8.4，自动化测试只连接项目 Compose 的独立实例，并为每轮测试创建和精确删除 `ii_test_<随机标识>` 数据库。

## 安全与恢复

- 数据库 URL 和凭据只通过环境变量提供，不进入源码、日志、响应或测试数据；
- 测试引导 URL 必须指向 MySQL 自带 `mysql` 数据库；
- 测试清理仅允许删除符合本轮精确随机名称的数据库；
- 数据库不可用继续安全映射为 `persistence_unavailable`；
- 失败事务不得继续查询，业务写入保持全量回滚；
- 历史 PostgreSQL 验收记录保留但明确标记已被替代。

## 验收条件

- 空 MySQL 8.4 完成 `upgrade head → downgrade base`；
- Alembic 与 ORM 元数据一致，所有表使用 InnoDB；
- JSON、外键、CHECK、字段长度、大小写敏感唯一身份通过真实 MySQL 测试；
- UTC 感知时间和微秒精度往返一致，无时区时间被拒绝；
- 人工报告创建、重放、冲突、并发和失败回滚行为不变；
- 四类资源读取 API 契约不变；
- 测试拒绝 PostgreSQL、SQLite 和日常项目数据库；
- PostgreSQL 驱动、JSONB、Compose 服务和当前架构声明无残留；
- Ruff、格式、Mypy、全部测试和覆盖率门槛通过；
- 用户现有 MySQL 上完成受控空库迁移与连接验证。

## 验证证据

- 单一 `0001_mysql_initial` 已在隔离 MySQL 8.4 完成升级、ORM 一致性检查和降级；
- 中文 JSON、UTC 微秒、大小写敏感来源身份和精确唯一约束共 6 项测试通过；
- 完整业务回归、统一验证和用户现有 MySQL 冒烟仍待完成。
