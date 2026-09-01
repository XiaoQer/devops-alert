from sqlalchemy import inspect
from sqlalchemy.engine import Engine


def test_feishu_activity_summary_supports_four_thousand_characters(
    migrated_engine: Engine,
) -> None:
    columns = {
        column["name"]: column
        for column in inspect(migrated_engine).get_columns("operational_incident_activities")
    }

    assert columns["summary"]["type"].length == 4_000
