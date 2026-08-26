from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Engine


def test_head_allows_nullable_service_and_persists_entity_columns(
    alembic_config: Config,
    mysql_engine: Engine,
) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    inspector = inspect(mysql_engine)
    for table_name in ("signal_events", "alerts", "alert_groups"):
        columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        assert columns["service"]["nullable"] is True
        assert columns["entity_type"]["nullable"] is False
        assert columns["entity_key"]["nullable"] is False
        assert columns["entity_display_name"]["nullable"] is False

    group_columns = {column["name"]: column for column in inspector.get_columns("alert_groups")}
    for column_name in (
        "problem_key",
        "problem_type",
        "scope_type",
        "scope_key",
        "scope_display_name",
        "signature_version",
    ):
        assert group_columns[column_name]["nullable"] is False

    command.check(alembic_config)
    command.downgrade(alembic_config, "base")
