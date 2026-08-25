# ADR 0002：MySQL 8.4 持久化基线

## 状态

已接受，取代 ADR 0001 中的 PostgreSQL 数据库选择。

## 背景

项目仍处于开发阶段，没有需要保留的 PostgreSQL 数据。用户日常环境已有 MySQL 8.4，希望主数据库、迁移、测试和后续持久任务统一使用同一种数据库，避免维护双数据库兼容层。

## 决策

- MySQL 8.4 是唯一受支持的主数据库，不同时支持 PostgreSQL、MariaDB 或 SQLite；
- 使用 PyMySQL、SQLAlchemy 和 Alembic，连接地址只接受包含数据库名的 `mysql+pymysql` URL；
- 所有表使用 InnoDB、utf8mb4 和 `utf8mb4_bin`，事务隔离级别使用 READ COMMITTED；
- JSON 事实使用 MySQL JSON；时间使用 `DATETIME(6)`，应用边界只接受感知时间并统一为 UTC；
- 不迁移旧数据，删除旧 PostgreSQL 迁移链，从空库建立单一 `0001_mysql_initial`；
- 自动化测试使用项目独立 MySQL 8.4 Compose 实例，每轮只创建和删除名称可精确验证的随机测试数据库；
- 数据库凭据只由环境变量提供，不进入源码、镜像、日志、API、测试数据或事故快照。

## 结果

当前人工报告、幂等、事务、审计和资源读取使用同一 MySQL 行为基线。大小写敏感身份、UTC 微秒和 JSON 往返由真实 MySQL 测试证明。旧 PostgreSQL 文档只作为历史实施记录保留，并明确标记已被本决策替代。

若需要回退，只能回退应用代码并重新创建空库；本项目没有 PostgreSQL 数据回迁路径，也不承诺跨数据库迁移兼容性。
