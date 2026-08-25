# MySQL 8.4 持久化基线实施计划

> **供当前会话执行：** 必须使用 `superpowers:executing-plans` 逐任务实施。用户明确要求所有变更直接提交到 `main`，不得创建功能分支、Git worktree 或使用子 Agent。步骤使用复选框跟踪。

**目标：** 把后端从 PostgreSQL-only 完整切换为 MySQL 8.4-only，在不保留旧数据的前提下重建迁移、测试和运行基线，并保持现有人工报告与资源读取行为。

**架构：** SQLAlchemy 仓储边界保持不变，PyMySQL 连接 MySQL 8.4/InnoDB。自定义 `UtcDateTime` 负责 UTC 感知时间与 `DATETIME(6)` 往返；Alembic 使用单一 MySQL 初始迁移；测试通过专用 Compose 实例创建名称可验证的随机临时数据库。

**技术栈：** Python 3.13–3.14、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、PyMySQL、MySQL 8.4、Pytest、Ruff、Mypy、Docker Compose。

**规格：** `specs/active/mysql-persistence-baseline.md`；完整设计见 `docs/superpowers/specs/2026-08-24-mysql-persistence-baseline-design.md`。

## 全局约束

- MySQL 8.4 是唯一受支持数据库，不保留 PostgreSQL、MariaDB 或 SQLite 兼容分支。
- 不迁移或保留 PostgreSQL 数据；旧 `0001/0002` 迁移由单一 `0001_mysql_initial` 取代。
- 所有表使用 InnoDB、utf8mb4 和大小写敏感排序规则；事务隔离使用 READ COMMITTED。
- MySQL 时间列使用 `DATETIME(6)`；Python 边界只接受并返回 UTC 感知时间。
- 日常数据库凭据与测试凭据只通过环境变量提供，不进入源码、日志、响应、测试数据或文档示例值。
- 测试引导 URL 必须是 `mysql+pymysql` 且数据库名严格为 `mysql`。
- 测试只删除与本轮 UUID 精确匹配的 `ii_test_<32位十六进制>` 数据库。
- 现有 SignalEvent、Alert、Incident、DiagnosisRun 模型和三套状态机业务语义不变。
- 本计划不实现共享信号接入、Alertmanager、CloudEvents、关联、取证或 AI。
- 每项行为改动先运行失败测试，再实现最小改动；每个任务提交前运行聚焦验证。

---

### 任务 1：切换依赖、配置和 MySQL 连接边界

**文件：**
- 修改：`backend/pyproject.toml`
- 修改：`backend/requirements.lock`
- 修改：`backend/src/incident_intelligence/settings.py`
- 修改：`backend/src/incident_intelligence/persistence/session.py`
- 创建：`backend/tests/unit/persistence/test_session.py`
- 修改：`backend/tests/conftest.py`
- 修改：`backend/tests/api/test_manual_reports.py`
- 修改：`backend/tests/api/test_resources.py`

**接口：**
- 产生：`validate_mysql_database_url(value: str) -> str`
- 保持：`get_engine(database_url: str) -> Engine`
- 保持：`make_session_factory(engine: Engine) -> sessionmaker[Session]`

- [x] **步骤 1：编写 MySQL URL 和连接配置失败测试**

创建 `test_session.py`，使用字面量 URL，断言 PostgreSQL、SQLite、缺少数据库名和非 PyMySQL 驱动被拒绝；合法 URL 产生 MySQL engine，并固定 READ COMMITTED、连接池预检查和 utf8mb4：

```python
@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://user:pass@db/app",
        "sqlite:///local.db",
        "mysql+mysqldb://user:pass@db/app",
        "mysql+pymysql://user:pass@db",
    ],
)
def test_settings_rejects_non_pymysql_or_missing_database(database_url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=database_url, api_token=SecretStr("x" * 32))


def test_engine_uses_mysql_read_committed_and_utf8mb4() -> None:
    engine = get_engine("mysql+pymysql://user:pass@127.0.0.1/app")
    assert engine.url.drivername == "mysql+pymysql"
    assert engine.dialect.name == "mysql"
    assert engine.pool._pre_ping is True
```

同时把测试默认 Settings URL 改为 `mysql+pymysql://unused:unused@127.0.0.1/unused`，保证 API 单元测试不依赖真实连接。

- [x] **步骤 2：运行测试并确认驱动或 URL 校验缺失**

运行：

```bash
cd backend
.venv/bin/python -m pytest tests/unit/persistence/test_session.py tests/api/test_health.py -q
```

预期：测试因 Settings 接受非 MySQL URL、PyMySQL 尚未安装或 engine 未固定 MySQL 配置而失败。

- [x] **步骤 3：替换依赖锁文件**

把生产依赖从：

```toml
"psycopg[binary]>=3.2,<4"
```

改为：

```toml
"PyMySQL>=1.1,<2"
```

使用现有虚拟环境执行：

```bash
cd backend
.venv/bin/python -m piptools compile --all-extras --output-file=requirements.lock --strip-extras pyproject.toml
.venv/bin/python -m pip install -r requirements.lock
```

确认 lock 中存在 `pymysql`，不存在 `psycopg` 和 `psycopg-binary`。

- [x] **步骤 4：实现 MySQL-only 配置和 engine**

Settings 使用 Pydantic 字段校验：

```python
@field_validator("database_url")
@classmethod
def validate_mysql_database_url(cls, value: str) -> str:
    url = make_url(value)
    if url.drivername != "mysql+pymysql" or not url.database:
        raise ValueError("database_url 必须是包含数据库名的 mysql+pymysql URL")
    return value
```

`get_engine` 使用：

```python
return create_engine(
    database_url,
    pool_pre_ping=True,
    isolation_level="READ COMMITTED",
    connect_args={"charset": "utf8mb4"},
)
```

在 MySQL connect 事件中执行固定字面量 `SET time_zone = '+00:00'`。不得记录 URL 或连接参数。

- [x] **步骤 5：运行聚焦测试与静态检查**

运行：

```bash
cd backend
.venv/bin/python -m pytest tests/unit/persistence/test_session.py tests/api/test_health.py -q
.venv/bin/ruff check src tests
.venv/bin/mypy src
```

预期：URL、engine 和健康检查测试通过；Ruff、Mypy 无错误。

- [ ] **步骤 6：提交连接基线**

```bash
git add backend/pyproject.toml backend/requirements.lock \
  backend/src/incident_intelligence/settings.py \
  backend/src/incident_intelligence/persistence/session.py \
  backend/tests/unit/persistence/test_session.py backend/tests/conftest.py \
  backend/tests/api/test_manual_reports.py backend/tests/api/test_resources.py
git commit -m "feat: 切换 MySQL 连接基线"
```

---

### 任务 2：实现 UTC DATETIME(6) 与 MySQL ORM 元数据

**文件：**
- 创建：`backend/src/incident_intelligence/persistence/types.py`
- 修改：`backend/src/incident_intelligence/persistence/models.py`
- 创建：`backend/tests/unit/persistence/test_types.py`

**接口：**
- 产生：`class UtcDateTime(TypeDecorator[datetime])`
- 消费：所有 ORM 时间列使用 `UtcDateTime()`

- [ ] **步骤 1：编写 UTC 类型失败测试**

单元测试直接调用类型处理器，断言 UTC、东八区、无时区和微秒：

```python
def test_utc_datetime_stores_naive_utc_and_restores_aware_utc() -> None:
    column_type = UtcDateTime()
    source = datetime(2026, 8, 25, 16, 0, 0, 123456, tzinfo=timezone(timedelta(hours=8)))
    stored = column_type.process_bind_param(source, mysql.dialect())
    assert stored == datetime(2026, 8, 25, 8, 0, 0, 123456)
    restored = column_type.process_result_value(stored, mysql.dialect())
    assert restored == datetime(2026, 8, 25, 8, 0, 0, 123456, tzinfo=UTC)


def test_utc_datetime_rejects_naive_value() -> None:
    with pytest.raises(ValueError, match="UTC"):
        UtcDateTime().process_bind_param(datetime(2026, 8, 25, 8, 0), mysql.dialect())
```

- [ ] **步骤 2：运行测试并确认 UTC 类型不存在**

运行：`cd backend && .venv/bin/python -m pytest tests/unit/persistence/test_types.py -q`

预期：导入失败，明确指向 `persistence.types.UtcDateTime` 尚未实现。

- [ ] **步骤 3：实现 UTC 类型并替换 ORM 方言类型**

实现：

```python
class UtcDateTime(TypeDecorator[datetime]):
    impl = mysql.DATETIME(fsp=6)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间必须包含 UTC 偏移")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)
```

`models.py` 使用 SQLAlchemy 通用 `JSON` 代替 JSONB，所有时间列使用 `UtcDateTime()`。每张表的 `__table_args__` 末尾增加：

```python
{"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}
```

- [ ] **步骤 4：运行单元测试和 ORM 静态检查**

运行：

```bash
cd backend
.venv/bin/python -m pytest tests/unit/persistence/test_types.py tests/unit/domain -q
.venv/bin/ruff check src tests
.venv/bin/mypy src
```

预期：UTC 类型和全部纯领域测试通过。

- [ ] **步骤 5：提交 MySQL ORM 类型**

```bash
git add backend/src/incident_intelligence/persistence/types.py \
  backend/src/incident_intelligence/persistence/models.py \
  backend/tests/unit/persistence/test_types.py
git commit -m "feat: 增加 MySQL UTC 与 JSON 映射"
```

---

### 任务 3：建立隔离的 MySQL Compose 与测试数据库生命周期

**文件：**
- 修改：`compose.yaml`
- 修改：`backend/tests/conftest.py`
- 创建：`backend/tests/support/__init__.py`
- 创建：`backend/tests/support/database.py`
- 创建：`backend/tests/unit/persistence/test_database_guard.py`
- 创建：`backend/tests/integration/persistence/test_mysql_connection.py`

**接口：**
- 产生：测试工具 `validate_test_database_name(name: str, expected: str) -> None`
- 产生：session fixture `mysql_engine() -> Iterator[Engine]`
- 保持：`alembic_config`、`migrated_engine`（迁移在任务 4 恢复）

- [ ] **步骤 1：编写测试 URL 和删除保护失败测试**

覆盖非 MySQL、错误驱动、数据库名不是 `mysql`、错误前缀、不同 UUID 和合法精确名称：

```python
@pytest.mark.parametrize("name", ["incident_intelligence", "ii_test_", "ii_test_nothex"])
def test_database_guard_rejects_unsafe_name(name: str) -> None:
    with pytest.raises(ValueError):
        validate_test_database_name(name, "ii_test_" + "a" * 32)


def test_database_guard_accepts_only_exact_generated_name() -> None:
    name = "ii_test_" + "a" * 32
    validate_test_database_name(name, name)
```

- [ ] **步骤 2：运行测试并确认保护函数不存在**

运行：`cd backend && .venv/bin/python -m pytest tests/unit/persistence/test_database_guard.py -q`

预期：导入失败，明确指向测试数据库保护模块尚未实现。

- [ ] **步骤 3：把 Compose 改为独立 MySQL 8.4**

服务名改为 `mysql-test`，配置只从环境读取：

```yaml
services:
  mysql-test:
    image: mysql:8.4
    environment:
      MYSQL_ROOT_PASSWORD: ${II_MYSQL_TEST_ROOT_PASSWORD:?请设置测试数据库密码}
    ports:
      - "127.0.0.1:${II_MYSQL_TEST_PORT:-43306}:3306"
    command: ["--character-set-server=utf8mb4", "--collation-server=utf8mb4_bin"]
    healthcheck:
      test: ["CMD-SHELL", "mysqladmin ping -h 127.0.0.1 -uroot -p$$MYSQL_ROOT_PASSWORD --silent"]
      interval: 2s
      timeout: 3s
      retries: 30
```

卷名改为 `incident-intelligence-mysql-test`。仓库不提供默认密码。

- [ ] **步骤 4：实现临时数据库 fixture 与会话属性测试**

从 `II_TEST_DATABASE_URL` 读取 `mysql+pymysql://<凭据>@<主机>/mysql`，生成 `ii_test_<uuid4.hex>`。在 AUTOCOMMIT 连接中执行受控字面量：

```python
connection.exec_driver_sql(
    f"CREATE DATABASE `{database_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
)
isolated_url = parsed_url.set(database=database_name)
```

清理时先调用 `validate_test_database_name(database_name, expected_name)`，再执行精确 `DROP DATABASE`。fixture 名由 `postgres_engine` 改为 `mysql_engine`，所有测试引用同步改名。

`test_mysql_connection.py` 不执行 Alembic，只连接随机临时数据库并断言真实会话：

```python
with mysql_engine.connect() as connection:
    row = connection.exec_driver_sql(
        "SELECT @@transaction_isolation, @@session.time_zone, @@character_set_connection"
    ).one()
assert row == ("READ-COMMITTED", "+00:00", "utf8mb4")
```

- [ ] **步骤 5：启动项目测试 MySQL 并验证隔离生命周期**

使用当前终端随机密码：

```bash
export II_MYSQL_TEST_ROOT_PASSWORD='<当前终端随机密码>'
export II_MYSQL_TEST_PORT=43306
docker compose up -d --wait mysql-test
export II_MYSQL_TEST_BOOTSTRAP_URL='mysql+pymysql://root:<URL编码密码>@127.0.0.1:43306/mysql'
export II_TEST_DATABASE_URL="$II_MYSQL_TEST_BOOTSTRAP_URL"
cd backend
.venv/bin/python -m pytest \
  tests/unit/persistence/test_database_guard.py \
  tests/integration/persistence/test_mysql_connection.py -q
```

预期：随机数据库创建、连接属性断言和精确清理通过；项目日常数据库不受影响。

- [ ] **步骤 6：提交测试隔离能力**

```bash
git add compose.yaml backend/tests/conftest.py backend/tests/support \
  backend/tests/unit/persistence/test_database_guard.py \
  backend/tests/integration/persistence/test_mysql_connection.py
git commit -m "test: 建立隔离 MySQL 验证环境"
```

---

### 任务 4：重建单一 MySQL 初始迁移

**文件：**
- 删除：`backend/migrations/versions/0001_initial_domain.py`
- 删除：`backend/migrations/versions/0002_multi_source_signal_intake.py`
- 创建：`backend/migrations/versions/0001_mysql_initial.py`
- 修改：`backend/migrations/env.py`
- 删除：`backend/tests/integration/persistence/test_multi_source_migration.py`
- 修改：`backend/tests/integration/persistence/test_initial_migration.py`
- 创建：`backend/tests/integration/persistence/test_mysql_types.py`

**接口：**
- 产生：Alembic revision `0001_mysql_initial`
- 保持：`upgrade head`、`downgrade base`、`command.check`

- [ ] **步骤 1：把迁移测试改成 MySQL 初始基线并运行失败**

迁移测试只接受七张当前表，断言 InnoDB、JSON 和 `datetime(6)`：

```python
def test_upgrade_creates_mysql_domain_tables(alembic_config: Config, mysql_engine: Engine) -> None:
    command.upgrade(alembic_config, "head")
    inspector = inspect(mysql_engine)
    assert set(inspector.get_table_names()) >= EXPECTED_TABLES
    assert inspector.get_table_options("signal_events")["mysql_engine"] == "InnoDB"
    assert str(next(c for c in inspector.get_columns("signal_events") if c["name"] == "facts")["type"]) == "JSON"
```

删除历史数据回填测试，因为用户明确不保留 PostgreSQL 数据。运行后预期旧迁移因 JSONB 或 PostgreSQL DDL 在 MySQL 失败。

- [ ] **步骤 2：创建完整 `0001_mysql_initial`**

迁移使用 `sqlalchemy.dialects.mysql.JSON` 和 `mysql.DATETIME(fsp=6)`，按以下依赖顺序建表：

```text
signal_events → alerts → incidents → diagnosis_runs
signal_events + alerts → signal_intake_results
ingestion_keys
audit_events
```

每个 `op.create_table` 设置：

```python
mysql_engine="InnoDB",
mysql_charset="utf8mb4",
mysql_collate="utf8mb4_bin",
```

完整复制 ORM 的主键、外键、CHECK、唯一约束和索引。降级按反向依赖顺序删除表。

- [ ] **步骤 3：让 Alembic 明确拒绝非 MySQL URL**

`env.py` 使用 `make_url` 校验 `II_DATABASE_URL`：驱动必须为 `mysql+pymysql` 且数据库名非空；错误消息只描述要求，不回显 URL。传入测试 engine 时同样断言 `engine.dialect.name == "mysql"`。

- [ ] **步骤 4：编写并运行真实 MySQL JSON、大小写和时间往返测试**

`test_mysql_types.py` 使用 `migrated_engine`，写入包含中文 JSON 和微秒 UTC 时间的 SignalEventRow，读取后断言：

```python
assert row.facts == {"region": "华东"}
assert row.observed_at == datetime(2026, 8, 25, 8, 0, 0, 123456, tzinfo=UTC)
```

再写入仅大小写不同的来源身份，断言两条均可存在；写入完全相同身份时断言 IntegrityError。运行：

```bash
cd backend
II_TEST_DATABASE_URL="$II_MYSQL_TEST_BOOTSTRAP_URL" .venv/bin/python -m pytest \
  tests/integration/persistence/test_initial_migration.py \
  tests/integration/persistence/test_mysql_types.py -q
```

预期：升级、降级、ORM 一致性、JSON、大小写和 UTC 微秒往返通过。

- [ ] **步骤 5：提交 MySQL 迁移基线**

```bash
git add backend/migrations backend/tests/integration/persistence
git commit -m "feat: 重建 MySQL 初始迁移"
```

---

### 任务 5：恢复数据库约束、人工报告和资源读取回归

**文件：**
- 修改：`backend/tests/integration/persistence/test_constraints.py`
- 修改：`backend/tests/integration/services/test_manual_intake.py`
- 修改：`backend/tests/api/test_manual_reports.py`
- 修改：`backend/tests/api/test_resources.py`
- 修改：`backend/src/incident_intelligence/persistence/repositories.py`（仅在 MySQL 方言暴露兼容问题时）
- 修改：`backend/src/incident_intelligence/persistence/unit_of_work.py`（仅在事务回滚测试暴露兼容问题时）

**接口：**
- 保持：人工报告服务与现有 HTTP 请求/响应契约
- 保持：四类资源读取响应字段

- [ ] **步骤 1：把 PostgreSQL 专用断言改为 MySQL 行为断言**

将 `test_signal_title_length_is_enforced_by_postgresql` 改名为 MySQL，并断言严格模式下 201 字符标题失败。增加真实约束测试：

```python
def make_alert(signal_event_id: str, **overrides: object) -> AlertRow:
    values: dict[str, object] = {
        "id": new_id("alt"),
        "signal_event_id": signal_event_id,
        "source": "alertmanager",
        "source_instance": "a" * 64,
        "source_alert_key": "payment-high-error-rate",
        "state": "ACTIVE",
        "title": "支付接口错误率升高",
        "severity": "high",
        "service": "payment-api",
        "environment": "production",
        "first_observed_at": NOW,
        "last_observed_at": NOW,
        "state_changed_at": NOW,
        "created_at": NOW,
        "version": 1,
    }
    values.update(overrides)
    return AlertRow(**values)


def test_source_identity_is_case_sensitive(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        session.add_all(
            [
                make_signal(source_event_id="Event-A"),
                make_signal(id=new_id("sig"), source_event_id="event-a"),
            ]
        )
        session.commit()
        assert session.scalar(select(func.count()).select_from(SignalEventRow)) == 2


def test_exact_alert_identity_is_unique(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        first_signal = make_signal(source_event_id="event-1")
        second_signal = make_signal(id=new_id("sig"), source_event_id="event-2")
        session.add_all([first_signal, second_signal])
        session.flush()
        session.add_all(
            [
                make_alert(first_signal.id),
                make_alert(second_signal.id, id=new_id("alt")),
            ]
        )
        with pytest.raises(IntegrityError):
            session.commit()
```

不得用 mock 替代真实 MySQL 约束。

所有测试只断言异常类别和最终行数，不断言 MySQL 原始错误文本。

- [ ] **步骤 2：运行聚焦回归并记录真实失败**

```bash
cd backend
II_TEST_DATABASE_URL="$II_MYSQL_TEST_BOOTSTRAP_URL" .venv/bin/python -m pytest \
  tests/integration/persistence \
  tests/integration/services/test_manual_intake.py \
  tests/api/test_manual_reports.py \
  tests/api/test_resources.py -q
```

预期：若仓储、事务或响应时间格式仍有方言问题，测试会明确失败；不得提前增加双数据库分支。

- [ ] **步骤 3：修复最小 MySQL 兼容问题**

只修改测试实际暴露的仓储或事务问题。保持：

- 单次人工报告在一个事务内创建四个领域对象和四条审计；
- 相同内容安全重放、不同内容冲突；
- 并发重复只形成一套记录；
- 任一步失败全量回滚；
- API 错误不回显数据库连接信息；
- 资源读取只查询目标表。

- [ ] **步骤 4：运行完整现有能力回归**

```bash
cd backend
II_TEST_DATABASE_URL="$II_MYSQL_TEST_BOOTSTRAP_URL" .venv/bin/python -m pytest -q
```

预期：全部现有测试在真实 MySQL 通过，测试数量不得少于切换前 107 项；删除的历史回填测试必须由新的 MySQL 迁移、UTC、JSON和大小写测试替代。

- [ ] **步骤 5：提交业务回归适配**

```bash
git add backend/src backend/tests
git commit -m "test: 恢复 MySQL 业务回归"
```

---

### 任务 6：统一文档、完整验收和用户现有 MySQL 冒烟

**文件：**
- 修改：`docs/architecture.md`
- 修改：`docs/current-state.md`
- 修改：`docs/decisions/0001-python-vue-technology-baseline.md`
- 创建：`docs/decisions/0002-mysql-persistence-baseline.md`
- 修改：`specs/active/multi-source-incident-center.md`
- 修改：`specs/active/multi-source-signal-intake.md`
- 修改：`specs/active/mysql-persistence-baseline.md`
- 修改：`docs/superpowers/specs/2026-08-24-multi-source-incident-center-design.md`
- 修改：`docs/superpowers/specs/2026-08-24-multi-source-signal-intake-design.md`
- 修改：`docs/superpowers/plans/2026-08-24-backend-foundation-manual-intake.md`
- 修改：`docs/superpowers/plans/2026-08-24-multi-source-signal-intake.md`
- 修改：`docs/verification/2026-08-24-backend-foundation.md`
- 创建：`docs/verification/2026-08-25-mysql-persistence-baseline.md`
- 修改：`README.md`

**接口：**
- 产生：当前唯一 MySQL 8.4 架构事实和可复现验收记录

- [ ] **步骤 1：更新当前架构和长期决策**

把所有当前态 PostgreSQL 描述改为 MySQL 8.4、InnoDB、PyMySQL、READ COMMITTED 和 UTC DATETIME(6)。`0002` 决策明确：无数据保留、MySQL-only、旧迁移替换、测试隔离和回退策略。

历史计划和验收文档不得伪造原结果；在文件开头增加：

```markdown
> 历史说明：本文记录 PostgreSQL 基线当时的实施与验证事实；当前持久化基线已由 MySQL 8.4 取代。
```

多源信号规格删除“回填现有 PostgreSQL 人工数据”验收条件，改为“从空 MySQL 建立完整当前结构”。

- [ ] **步骤 2：更新 README 本地运行说明**

日常运行示例只包含占位符：

```bash
export II_DATABASE_URL='mysql+pymysql://<用户>:<URL编码密码>@127.0.0.1:3307/incident_intelligence'
export II_API_TOKEN='<本地随机 Token>'
cd backend
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m uvicorn incident_intelligence.main:create_app --factory --host 127.0.0.1 --port 8000
```

测试示例要求用户现场生成密码，不在仓库写任何固定凭据。

- [ ] **步骤 3：运行项目独立 MySQL 的统一验证**

```bash
II_TEST_DATABASE_URL="$II_MYSQL_TEST_BOOTSTRAP_URL" ./scripts/verify-backend.sh
```

必须确认 Ruff、格式、Mypy、全部测试和覆盖率门槛通过，并记录精确测试数与覆盖率。

- [ ] **步骤 4：扫描 PostgreSQL 运行残留与 Secret**

```bash
rg -n "psycopg|JSONB|postgresql\+|postgres:16|II_POSTGRES" backend compose.yaml README.md docs specs
```

允许命中仅限已加“历史说明”的历史事实和新决策中解释被替代原因；当前代码、依赖、Compose、README、活跃规格和当前架构不得命中。Secret 形态扫描不得输出实际环境变量值。

- [ ] **步骤 5：在用户现有 MySQL 8.4 上执行受控空库冒烟**

用户在当前终端提供选择 MySQL 自带 `mysql` 库的 `II_MYSQL_BOOTSTRAP_URL`。先只读确认服务版本，并查询 `information_schema.schemata` 确认 `incident_intelligence` 不存在；若已存在则停止，不删除、不覆盖。确认不存在后，以 utf8mb4/utf8mb4_bin 创建该专用库，再由用户设置不回显的 `II_DATABASE_URL`。执行：

```bash
cd backend
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic current
```

随后启动 API，只记录 `/health/ready` 状态码和数据库 available 状态。不得输出 URL、用户名、密码或容器环境。若目标库非空，立即停止并请求用户处理，不删除任何表或数据库。

- [ ] **步骤 6：记录证据并验收规格**

`docs/verification/2026-08-25-mysql-persistence-baseline.md` 记录：MySQL 版本、迁移 revision、表数量、测试数、覆盖率、静态检查、UTC/JSON/大小写/事务证据和真实健康检查结果，不记录 Secret。

满足全部验收条件后：

- 把 `specs/active/mysql-persistence-baseline.md` 状态改为“已验收”并移入 `specs/completed/`；
- 更新 `docs/current-state.md`，明确 MySQL 已可用；
- 多源信号接入仍标记为实施中，外部入口仍不可用；
- 下一步恢复共享信号接入服务任务 3。

- [ ] **步骤 7：停止并清理项目测试环境**

停止 Compose 测试 MySQL，并删除本项目测试卷；不得操作用户现有 `devops-assistant-mysql-1` 容器或其数据。清除当前终端测试密码变量。

- [ ] **步骤 8：提交 MySQL 基线验收**

```bash
git add README.md compose.yaml backend docs specs
git commit -m "docs: 验收 MySQL 持久化基线"
```

---

## 完成定义

- 六个任务均有红绿测试或可执行验证证据；
- MySQL 是代码、依赖、迁移、Compose、活跃规格和当前文档中的唯一数据库；
- 完整现有能力在真实 MySQL 8.4 上通过；
- 用户现有 MySQL 只新增专用项目结构，不改动其他数据库或容器；
- 工作区位于 `main` 且干净；
- 共享信号接入仍明确标记为下一阶段，未被误报为已实现。
