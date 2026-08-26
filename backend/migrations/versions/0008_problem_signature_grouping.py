"""为告警组增加版本化问题签名。

Revision ID: 0008_problem_signature_grouping
Revises: 0007_entity_aware_alerts
Create Date: 2026-08-26
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256

import sqlalchemy as sa
from alembic import op

revision: str = "0008_problem_signature_grouping"
down_revision: str | None = "0007_entity_aware_alerts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIGNATURE_VERSION = "problem-signature.v1"


def upgrade() -> None:
    op.add_column("alert_groups", sa.Column("problem_key", sa.String(64), nullable=True))
    op.add_column("alert_groups", sa.Column("problem_type", sa.String(200), nullable=True))
    op.add_column("alert_groups", sa.Column("scope_type", sa.String(16), nullable=True))
    op.add_column("alert_groups", sa.Column("scope_key", sa.String(64), nullable=True))
    op.add_column("alert_groups", sa.Column("scope_display_name", sa.String(257), nullable=True))
    op.add_column("alert_groups", sa.Column("signature_version", sa.String(32), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT g.id, g.title, g.symptom, g.environment, g.service, "
            "a.alert_source_id, s.facts "
            "FROM alert_groups g "
            "JOIN alerts a ON a.id = g.representative_alert_id "
            "JOIN signal_events s ON s.id = a.signal_event_id"
        )
    ).mappings()
    for row in rows:
        facts = _facts(row["facts"])
        if row["service"]:
            facts["service"] = str(row["service"])
        values = _signature_values(
            alert_source_id=str(row["alert_source_id"]),
            problem_type=str(facts.get("alertname") or row["title"]),
            symptom=str(row["symptom"]),
            environment=str(row["environment"]),
            facts=facts,
        )
        connection.execute(
            sa.text(
                "UPDATE alert_groups SET problem_key=:problem_key, "
                "problem_type=:problem_type, scope_type=:scope_type, scope_key=:scope_key, "
                "scope_display_name=:scope_display_name, signature_version=:signature_version "
                "WHERE id=:id"
            ),
            {"id": row["id"], **values},
        )

    for column_name, column_type in (
        ("problem_key", sa.String(64)),
        ("problem_type", sa.String(200)),
        ("scope_type", sa.String(16)),
        ("scope_key", sa.String(64)),
        ("scope_display_name", sa.String(257)),
        ("signature_version", sa.String(32)),
    ):
        op.alter_column("alert_groups", column_name, existing_type=column_type, nullable=False)

    op.create_check_constraint(
        op.f("ck_alert_groups_alert_group_problem_key"),
        "alert_groups",
        "char_length(problem_key) = 64",
    )
    op.create_check_constraint(
        op.f("ck_alert_groups_alert_group_scope_key"),
        "alert_groups",
        "char_length(scope_key) = 64",
    )
    op.create_check_constraint(
        op.f("ck_alert_groups_alert_group_scope_type"),
        "alert_groups",
        "scope_type IN ('SERVICE','WORKLOAD','NAMESPACE','CLUSTER','JOB','SOURCE')",
    )
    op.create_check_constraint(
        op.f("ck_alert_groups_alert_group_signature_version"),
        "alert_groups",
        "signature_version = 'problem-signature.v1'",
    )
    op.drop_index("ix_alert_groups_candidate", table_name="alert_groups")
    op.create_index(
        "ix_alert_groups_candidate",
        "alert_groups",
        ["state", "problem_key", "last_observed_at"],
    )
    _replace_correlation_outcome_constraints(include_service_missing=True)


def downgrade() -> None:
    connection = op.get_bind()
    for table_name in ("alert_group_decisions", "correlation_decisions"):
        connection.execute(
            sa.text(
                f"UPDATE {table_name} SET outcome='REJECTED_INELIGIBLE' "
                "WHERE outcome='SKIPPED_SERVICE_MISSING'"
            )
        )
    _replace_correlation_outcome_constraints(include_service_missing=False)
    op.drop_index("ix_alert_groups_candidate", table_name="alert_groups")
    op.create_index(
        "ix_alert_groups_candidate",
        "alert_groups",
        ["state", "entity_key", "environment", "symptom", "last_observed_at"],
    )
    for constraint_name in (
        "ck_alert_groups_alert_group_signature_version",
        "ck_alert_groups_alert_group_scope_type",
        "ck_alert_groups_alert_group_scope_key",
        "ck_alert_groups_alert_group_problem_key",
    ):
        op.drop_constraint(op.f(constraint_name), "alert_groups", type_="check")
    for column_name in (
        "signature_version",
        "scope_display_name",
        "scope_key",
        "scope_type",
        "problem_type",
        "problem_key",
    ):
        op.drop_column("alert_groups", column_name)


def _replace_correlation_outcome_constraints(*, include_service_missing: bool) -> None:
    values = (
        "'CREATED_NO_MATCH', 'LINKED_EXACT_SERVICE', 'LINKED_EXISTING', "
        "'CREATED_AMBIGUOUS', 'CREATED_DEPENDENCY_CANDIDATE', "
        "'REJECTED_INELIGIBLE', 'RECORDED_RESOLUTION', 'SUPERSEDED'"
    )
    if include_service_missing:
        values = f"'SKIPPED_SERVICE_MISSING', {values}"
    for table_name, constraint_name in (
        ("alert_group_decisions", "alert_group_decision_outcome"),
        ("correlation_decisions", "correlation_decision_outcome"),
    ):
        op.drop_constraint(op.f(f"ck_{table_name}_{constraint_name}"), table_name, type_="check")
        op.create_check_constraint(
            op.f(f"ck_{table_name}_{constraint_name}"),
            table_name,
            f"outcome IN ({values})",
        )


def _signature_values(
    *,
    alert_source_id: str,
    problem_type: str,
    symptom: str,
    environment: str,
    facts: Mapping[str, str],
) -> dict[str, str]:
    normalized_problem_type = _bounded(problem_type, "未命名告警", 200)
    scope_type, scope_parts, scope_display_name = _scope(alert_source_id, facts)
    scope_key = _digest("problem-scope.v1", scope_type, *scope_parts)
    return {
        "problem_key": _digest(
            SIGNATURE_VERSION,
            alert_source_id,
            normalized_problem_type.casefold(),
            _bounded(symptom, "unknown", 64).casefold(),
            _bounded(environment, "unknown", 32).casefold(),
            scope_type,
            scope_key,
        ),
        "problem_type": normalized_problem_type,
        "scope_type": scope_type,
        "scope_key": scope_key,
        "scope_display_name": scope_display_name,
        "signature_version": SIGNATURE_VERSION,
    }


def _scope(alert_source_id: str, facts: Mapping[str, str]) -> tuple[str, tuple[str, ...], str]:
    service = _value(facts, "service")
    if service:
        return "SERVICE", (service,), service
    cluster = _value(facts, "cluster")
    namespace = _value(facts, "namespace")
    workload = _value(facts, "workload")
    if workload:
        parts = tuple(item for item in (cluster, namespace, workload) if item)
        return "WORKLOAD", parts, "/".join(parts)
    if namespace:
        parts = tuple(item for item in (cluster, namespace) if item)
        return "NAMESPACE", parts, "/".join(parts)
    if cluster:
        return "CLUSTER", (cluster,), cluster
    job = _value(facts, "job")
    if job:
        return "JOB", (job,), job
    return "SOURCE", (alert_source_id,), alert_source_id


def _facts(value: object) -> dict[str, str]:
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(item) for key, item in parsed.items() if isinstance(item, str)}


def _value(values: Mapping[str, str], key: str) -> str | None:
    value = values.get(key)
    normalized = " ".join(value.split()) if isinstance(value, str) else ""
    return normalized or None


def _bounded(value: str, fallback: str, limit: int) -> str:
    normalized = " ".join(value.split()) if isinstance(value, str) else ""
    return (normalized or fallback)[:limit]


def _digest(*parts: str) -> str:
    return sha256("\0".join(parts).encode("utf-8")).hexdigest()
