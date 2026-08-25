from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import URL, Connection, Engine, make_url

from incident_intelligence.persistence import models as persistence_models  # noqa: F401
from incident_intelligence.persistence.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _validate_database_url(value: str) -> URL:
    try:
        database_url = make_url(value)
    except (TypeError, ValueError) as error:
        raise RuntimeError("数据库连接地址必须是包含数据库名的 mysql+pymysql URL") from error
    if database_url.drivername != "mysql+pymysql" or not database_url.database:
        raise RuntimeError("数据库连接地址必须是包含数据库名的 mysql+pymysql URL")
    return database_url


def _configured_database_url() -> str:
    database_url = os.environ.get("II_DATABASE_URL")
    if database_url is None:
        raise RuntimeError("需要通过 II_DATABASE_URL 提供 MySQL 连接地址")
    _validate_database_url(database_url)
    return database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_configured_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    provided_engine = config.attributes.get("engine")
    if isinstance(provided_engine, Engine):
        if provided_engine.dialect.name != "mysql":
            raise RuntimeError("Alembic 只支持 MySQL engine")
        with provided_engine.connect() as connection:
            _run_with_connection(connection)
        return

    config.set_main_option("sqlalchemy.url", _configured_database_url().replace("%", "%%"))
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _run_with_connection(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
