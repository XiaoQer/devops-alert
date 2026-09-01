from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from incident_intelligence.persistence.base import Base
from incident_intelligence.persistence.types import UtcDateTime

SEVERITY_VALUES = "'critical', 'high', 'medium', 'low'"
ENVIRONMENT_CHECK = "environment REGEXP '^[a-z][a-z0-9-]{0,31}$'"
EVENT_TYPE_VALUES = "'manual.reported', 'alert.firing', 'alert.resolved'"
PROJECTION_OUTCOME_VALUES = (
    "'opened', 'updated', 'resolved', 'reopened', 'stale', 'orphan_resolved'"
)
CATALOG_STATE_VALUES = "'ACTIVE', 'INACTIVE'"
CORRELATION_JOB_STATE_VALUES = "'PENDING', 'LEASED', 'SUCCEEDED', 'FAILED'"
ALERT_GROUP_STATE_VALUES = "'FORMING', 'ACTIVE', 'OBSERVING', 'CLOSED'"
ALERT_GROUP_MEMBERSHIP_STATE_VALUES = "'AUTO_CONFIRMED', 'MANUAL_CONFIRMED', 'PENDING', 'REMOVED'"
ALERT_EVENT_OPERATION_KIND_VALUES = "'CONFIRM', 'SPLIT', 'MERGE'"
ALERT_EVENT_LIFECYCLE_ACTION_VALUES = "'FORMING_COMPLETE', 'OBSERVATION_COMPLETE'"
STORM_STATE_VALUES = "'NORMAL', 'STORM'"
CORRELATION_OUTCOME_VALUES = (
    "'SKIPPED_SERVICE_MISSING', 'CREATED_NO_MATCH', "
    "'LINKED_EXACT_SERVICE', 'LINKED_EXISTING', "
    "'CREATED_AMBIGUOUS', 'CREATED_DEPENDENCY_CANDIDATE', "
    "'REJECTED_INELIGIBLE', 'RECORDED_RESOLUTION', 'SUPERSEDED'"
)
INCIDENT_STATE_VALUES = (
    "'DETECTED', 'TRIAGING', 'INVESTIGATING', 'MITIGATING', "
    "'MONITORING_RECOVERY', 'RESOLVED', 'CLOSED'"
)
INCIDENT_ACTIVITY_KIND_VALUES = (
    "'INCIDENT_CLAIMED', 'INCIDENT_RELEASED', 'STATE_TRANSITIONED', "
    "'NOTE_ADDED', 'INCIDENT_RESOLVED', 'INCIDENT_REOPENED', 'INCIDENT_CLOSED'"
)
INCIDENT_OPERATION_KIND_VALUES = (
    "'CLAIM', 'RELEASE', 'TRANSITION', 'ADD_NOTE', 'RESOLVE', 'REOPEN', 'CLOSE'"
)
INCIDENT_NOTE_CATEGORY_VALUES = (
    "'CURRENT_FINDING', 'ACTION_TAKEN', 'ACTION_RESULT', 'NEXT_STEP', 'GENERAL'"
)
INCIDENT_RESOLUTION_CATEGORY_VALUES = (
    "'RECOVERED', 'FALSE_POSITIVE', 'DUPLICATE', 'NO_ACTION', 'OTHER'"
)
ALERT_SOURCE_TYPE_VALUES = "'ALERTMANAGER', 'CLOUDEVENTS', 'MANUAL'"
ALERT_SOURCE_MANAGEMENT_VALUES = "'USER_MANAGED', 'SYSTEM_MANAGED'"
ALERT_SOURCE_STATE_VALUES = "'ENABLED', 'DISABLED'"
CREDENTIAL_STATE_VALUES = "'ACTIVE', 'REVOKED'"
RECEIPT_OUTCOME_VALUES = (
    "'ACCEPTED', 'REPLAYED', 'VALIDATED', 'PAYLOAD_REJECTED', "
    "'SOURCE_DISABLED', 'PROCESSING_FAILED'"
)
OPERATIONAL_INCIDENT_STATE_VALUES = "'OPEN', 'ACKNOWLEDGED', 'RESOLVED'"
OPERATIONAL_INCIDENT_ACTIVITY_VALUES = (
    "'INCIDENT_CREATED', 'ALERTS_LINKED', 'SEVERITY_ESCALATED', "
    "'ALL_ALERTS_RECOVERED', 'ACKNOWLEDGED', 'RESOLVED', "
    "'FEISHU_MESSAGE_RECORDED', 'NOTIFICATION_FAILED'"
)
INCIDENT_ASYNC_STATE_VALUES = "'PENDING', 'LEASED', 'SUCCEEDED', 'FAILED'"


def _mysql_table_options() -> dict[str, str]:
    return {
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_bin",
    }


class AlertSourceRow(Base):
    __tablename__ = "alert_sources"
    __table_args__ = (
        UniqueConstraint("name", name="alert_source_name"),
        CheckConstraint(f"source_type IN ({ALERT_SOURCE_TYPE_VALUES})", name="source_type"),
        CheckConstraint(
            f"management_type IN ({ALERT_SOURCE_MANAGEMENT_VALUES})", name="management_type"
        ),
        CheckConstraint(f"state IN ({ALERT_SOURCE_STATE_VALUES})", name="state"),
        CheckConstraint(ENVIRONMENT_CHECK, name="environment"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(
            "accepted_requests >= 0 AND rejected_requests >= 0 "
            "AND opened_count >= 0 AND updated_count >= 0 AND resolved_count >= 0 "
            "AND replayed_count >= 0 AND ignored_count >= 0",
            name="non_negative_counts",
        ),
        Index("ix_alert_sources_state", "state"),
        Index("ix_alert_sources_source_type", "source_type"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    management_type: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    environment_name: Mapped[str] = mapped_column(String(64), nullable=False)
    environment_configured: Mapped[bool] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    last_accepted_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    last_rejected_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    accepted_requests: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rejected_requests: Mapped[int] = mapped_column(BigInteger, nullable=False)
    opened_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resolved_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    replayed_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ignored_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class AlertSourceCredentialRow(Base):
    __tablename__ = "alert_source_credentials"
    __table_args__ = (
        UniqueConstraint("token_digest", name="alert_source_credential_digest"),
        CheckConstraint(f"state IN ({CREDENTIAL_STATE_VALUES})", name="state"),
        CheckConstraint(
            "(state = 'ACTIVE' AND revoked_at IS NULL AND revoked_by IS NULL) OR "
            "(state = 'REVOKED' AND revoked_at IS NOT NULL AND revoked_by IS NOT NULL)",
            name="revocation_pair",
        ),
        CheckConstraint("char_length(token_digest) = 64", name="token_digest"),
        Index("ix_alert_source_credentials_source_state", "alert_source_id", "state"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_source_id: Mapped[str] = mapped_column(
        ForeignKey("alert_sources.id", ondelete="RESTRICT"), nullable=False
    )
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class AlertSourceReceiptRow(Base):
    __tablename__ = "alert_source_receipts"
    __table_args__ = (
        CheckConstraint(f"adapter_type IN ({ALERT_SOURCE_TYPE_VALUES})", name="adapter_type"),
        CheckConstraint(f"outcome IN ({RECEIPT_OUTCOME_VALUES})", name="outcome"),
        CheckConstraint(
            "input_count >= 0 AND opened_count >= 0 AND updated_count >= 0 "
            "AND resolved_count >= 0 AND replayed_count >= 0 AND ignored_count >= 0",
            name="non_negative_counts",
        ),
        Index("ix_alert_source_receipts_source_time", "alert_source_id", "received_at", "id"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_source_id: Mapped[str] = mapped_column(
        ForeignKey("alert_sources.id", ondelete="RESTRICT"), nullable=False
    )
    adapter_type: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    input_count: Mapped[int] = mapped_column(nullable=False)
    opened_count: Mapped[int] = mapped_column(nullable=False)
    updated_count: Mapped[int] = mapped_column(nullable=False)
    resolved_count: Mapped[int] = mapped_column(nullable=False)
    replayed_count: Mapped[int] = mapped_column(nullable=False)
    ignored_count: Mapped[int] = mapped_column(nullable=False)
    received_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class AlertSourceOperationRow(Base):
    __tablename__ = "alert_source_operations"
    __table_args__ = (
        UniqueConstraint("scope", "idempotency_key_hash", name="alert_source_operation_key"),
        CheckConstraint("action IN ('CREATE', 'UPDATE', 'ROTATE', 'REVOKE')", name="action"),
        CheckConstraint(
            "char_length(idempotency_key_hash) = 64 AND char_length(command_fingerprint) = 64",
            name="hashes",
        ),
        CheckConstraint("result_version >= 1", name="result_version"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    command_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    alert_source_id: Mapped[str] = mapped_column(
        ForeignKey("alert_sources.id", ondelete="RESTRICT"), nullable=False
    )
    credential_id: Mapped[str | None] = mapped_column(
        ForeignKey("alert_source_credentials.id", ondelete="RESTRICT"), nullable=True
    )
    result_version: Mapped[int] = mapped_column(nullable=False)
    completed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class SignalEventRow(Base):
    __tablename__ = "signal_events"
    __table_args__ = (
        UniqueConstraint(
            "alert_source_id", "source", "source_event_id", name="signal_source_identity"
        ),
        CheckConstraint(f"severity IN ({SEVERITY_VALUES})", name="signal_severity"),
        CheckConstraint(ENVIRONMENT_CHECK, name="signal_environment"),
        CheckConstraint(f"event_type IN ({EVENT_TYPE_VALUES})", name="signal_event_type"),
        CheckConstraint(
            "entity_type IN ('SERVICE','WORKLOAD','POD','NODE','JOB',"
            "'INSTANCE','CLUSTER','UNKNOWN')",
            name="signal_entity_type",
        ),
        CheckConstraint("char_length(entity_key) = 64", name="signal_entity_key"),
        Index("ix_signal_events_observed_at", "observed_at"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_source_id: Mapped[str] = mapped_column(
        ForeignKey("alert_sources.id", ondelete="RESTRICT"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    source_alert_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    episode_started_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    alert_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(String(2_000), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2_000), nullable=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    service: Mapped[str | None] = mapped_column(String(128), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_display_name: Mapped[str] = mapped_column(String(257), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    facts: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)


class AlertRow(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint("state IN ('ACTIVE', 'RESOLVED', 'SUPPRESSED')", name="alert_state"),
        CheckConstraint("cycle >= 1", name="alert_cycle"),
        CheckConstraint(f"severity IN ({SEVERITY_VALUES})", name="alert_severity"),
        CheckConstraint(ENVIRONMENT_CHECK, name="alert_environment"),
        CheckConstraint("char_length(source_instance) = 64", name="alert_source_instance"),
        CheckConstraint("char_length(source_alert_key) >= 1", name="alert_source_alert_key"),
        CheckConstraint(
            "entity_type IN ('SERVICE','WORKLOAD','POD','NODE','JOB',"
            "'INSTANCE','CLUSTER','UNKNOWN')",
            name="alert_entity_type",
        ),
        CheckConstraint("char_length(entity_key) = 64", name="alert_entity_key"),
        UniqueConstraint(
            "alert_source_id",
            "source",
            "source_instance",
            "source_alert_key",
            name="alert_source_identity",
        ),
        Index("ix_alerts_state", "state"),
        Index("ix_alerts_signal_event_id", "signal_event_id"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    signal_event_id: Mapped[str] = mapped_column(
        ForeignKey("signal_events.id", ondelete="RESTRICT"), nullable=False
    )
    alert_source_id: Mapped[str] = mapped_column(
        ForeignKey("alert_sources.id", ondelete="RESTRICT"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_instance: Mapped[str] = mapped_column(String(64), nullable=False)
    source_alert_key: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    cycle: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    service: Mapped[str | None] = mapped_column(String(128), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_display_name: Mapped[str] = mapped_column(String(257), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    state_changed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)


class AlertLifecycleRow(Base):
    __tablename__ = "alert_lifecycles"
    __table_args__ = (
        CheckConstraint(f"severity IN ({SEVERITY_VALUES})", name="severity"),
        CheckConstraint(ENVIRONMENT_CHECK, name="environment"),
        CheckConstraint(
            "entity_type IN ('SERVICE','WORKLOAD','POD','NODE','JOB',"
            "'INSTANCE','CLUSTER','UNKNOWN')",
            name="entity_type",
        ),
        CheckConstraint("char_length(entity_key) = 64", name="entity_key"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(
            "(state = 'ACTIVE' AND resolved_at IS NULL AND firing_observed = 1) "
            "OR (state = 'RESOLVED' AND resolved_at IS NOT NULL "
            "AND resolved_at >= episode_started_at)",
            name="state",
        ),
        UniqueConstraint(
            "alert_source_id",
            "source_alert_key",
            "episode_started_at",
            name="alert_lifecycle_identity",
        ),
        Index("ix_alert_lifecycles_state", "state"),
        Index("ix_alert_lifecycles_source_received", "alert_source_id", "first_received_at"),
        Index("ix_alert_lifecycles_first_received_at", "first_received_at"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_source_id: Mapped[str] = mapped_column(
        ForeignKey("alert_sources.id", ondelete="RESTRICT"), nullable=False
    )
    source_alert_key: Mapped[str] = mapped_column(String(128), nullable=False)
    episode_started_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    alert_name: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(String(2_000), nullable=False)
    description: Mapped[str] = mapped_column(String(2_000), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    service: Mapped[str | None] = mapped_column(String(128), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_display_name: Mapped[str] = mapped_column(String(257), nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    first_received_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    last_received_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    firing_observed: Mapped[bool] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class IncidentRuleRow(Base):
    __tablename__ = "incident_rules"
    __table_args__ = (
        UniqueConstraint("name", name="rule_name"),
        CheckConstraint("state IN ('DRAFT', 'PUBLISHED', 'DISABLED')", name="state"),
        CheckConstraint(ENVIRONMENT_CHECK, name="environment"),
        CheckConstraint("group_by IN ('SERVICE', 'ENTITY')", name="group_by"),
        CheckConstraint("window_minutes BETWEEN 1 AND 60", name="window_minutes"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(
            "JSON_TYPE(alert_source_ids) = 'ARRAY' AND JSON_LENGTH(alert_source_ids) <= 50",
            name="source_ids",
        ),
        CheckConstraint(
            "JSON_TYPE(services) = 'ARRAY' AND JSON_LENGTH(services) <= 50",
            name="services",
        ),
        CheckConstraint(
            "JSON_TYPE(conditions) = 'ARRAY' AND JSON_LENGTH(conditions) BETWEEN 1 AND 4",
            name="conditions",
        ),
        CheckConstraint(
            "(last_successful_dry_run_id IS NULL "
            "AND last_successful_dry_run_version IS NULL "
            "AND last_successful_dry_run_at IS NULL) OR "
            "(last_successful_dry_run_id IS NOT NULL "
            "AND last_successful_dry_run_version BETWEEN 1 AND version "
            "AND last_successful_dry_run_at IS NOT NULL)",
            name="dry_run_pair",
        ),
        CheckConstraint(
            "(state = 'DRAFT' AND published_at IS NULL AND disabled_at IS NULL) OR "
            "(state = 'PUBLISHED' AND published_at IS NOT NULL AND disabled_at IS NULL) OR "
            "(state = 'DISABLED' AND published_at IS NOT NULL AND disabled_at IS NOT NULL)",
            name="state_times",
        ),
        Index("ix_incident_rules_state", "state"),
        Index("ix_incident_rules_updated_at", "updated_at", "id"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(1_000), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    alert_source_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    services: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    group_by: Mapped[str] = mapped_column(String(16), nullable=False)
    window_minutes: Mapped[int] = mapped_column(nullable=False)
    conditions: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    summary: Mapped[str] = mapped_column(String(2_000), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    last_successful_dry_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_successful_dry_run_version: Mapped[int | None] = mapped_column(nullable=True)
    last_successful_dry_run_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime(), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class IncidentRuleDryRunRow(Base):
    __tablename__ = "incident_rule_dry_runs"
    __table_args__ = (
        CheckConstraint("rule_version >= 1", name="rule_version"),
        CheckConstraint("history_hours IN (1, 6, 12, 24, 48)", name="history_hours"),
        CheckConstraint(
            "scanned_alert_count BETWEEN 0 AND 10001 AND match_count BETWEEN 0 AND 100",
            name="counts",
        ),
        CheckConstraint(
            "JSON_TYPE(matches) = 'ARRAY' AND JSON_LENGTH(matches) <= 100",
            name="matches",
        ),
        Index("ix_incident_rule_dry_runs_rule_time", "rule_id", "executed_at", "id"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rule_id: Mapped[str] = mapped_column(
        ForeignKey("incident_rules.id", ondelete="CASCADE"), nullable=False
    )
    rule_version: Mapped[int] = mapped_column(nullable=False)
    history_hours: Mapped[int] = mapped_column(nullable=False)
    scanned_alert_count: Mapped[int] = mapped_column(nullable=False)
    match_count: Mapped[int] = mapped_column(nullable=False)
    truncated: Mapped[bool] = mapped_column(nullable=False)
    matches: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class IncidentRuleOperationRow(Base):
    __tablename__ = "incident_rule_operations"
    __table_args__ = (
        UniqueConstraint("scope", "idempotency_key_hash", name="incident_rule_operation_key"),
        CheckConstraint(
            "action IN ('CREATE', 'UPDATE', 'DELETE', 'PUBLISH', 'DISABLE', 'COPY')",
            name="action",
        ),
        CheckConstraint(
            "char_length(idempotency_key_hash) = 64 AND char_length(command_fingerprint) = 64",
            name="hashes",
        ),
        CheckConstraint("result_version >= 1", name="result_version"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(96), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    command_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(36), nullable=False)
    result_version: Mapped[int] = mapped_column(nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class OperationalIncidentRow(Base):
    __tablename__ = "operational_incidents"
    __table_args__ = (
        UniqueConstraint("reference", name="operational_incident_reference"),
        UniqueConstraint("open_boundary_key", name="operational_incident_open_boundary"),
        CheckConstraint(
            f"state IN ({OPERATIONAL_INCIDENT_STATE_VALUES})",
            name="operational_incident_state",
        ),
        CheckConstraint(f"severity IN ({SEVERITY_VALUES})", name="operational_incident_severity"),
        CheckConstraint(ENVIRONMENT_CHECK, name="operational_incident_environment"),
        CheckConstraint("group_by IN ('SERVICE', 'ENTITY')", name="operational_incident_group_by"),
        CheckConstraint(
            "char_length(open_boundary_key) = 64 OR open_boundary_key IS NULL",
            name="operational_incident_open_boundary_length",
        ),
        CheckConstraint(
            "incident_rule_version >= 1 AND version >= 1",
            name="operational_incident_versions",
        ),
        CheckConstraint(
            "alert_count >= 1 AND active_alert_count >= 0 "
            "AND active_alert_count <= alert_count AND distinct_alert_name_count >= 1",
            name="operational_incident_counts",
        ),
        Index("ix_operational_incidents_list", "state", "updated_at", "id"),
        Index(
            "ix_operational_incidents_rule_group",
            "incident_rule_id",
            "environment",
            "group_key",
        ),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reference: Mapped[str] = mapped_column(String(22), nullable=False)
    title: Mapped[str] = mapped_column(String(320), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    group_by: Mapped[str] = mapped_column(String(16), nullable=False)
    group_key: Mapped[str] = mapped_column(String(257), nullable=False)
    group_display_name: Mapped[str] = mapped_column(String(257), nullable=False)
    incident_rule_id: Mapped[str] = mapped_column(
        ForeignKey("incident_rules.id", ondelete="RESTRICT"), nullable=False
    )
    incident_rule_version: Mapped[int] = mapped_column(nullable=False)
    open_boundary_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    alert_count: Mapped[int] = mapped_column(nullable=False)
    active_alert_count: Mapped[int] = mapped_column(nullable=False)
    distinct_alert_name_count: Mapped[int] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    opened_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    resolution_summary: Mapped[str | None] = mapped_column(String(2_000), nullable=True)
    last_alert_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class OperationalIncidentAlertRow(Base):
    __tablename__ = "operational_incident_alerts"
    __table_args__ = (
        CheckConstraint("incident_rule_version >= 1", name="operational_incident_alert_version"),
        Index("ix_operational_incident_alerts_alert", "alert_id", "incident_id"),
        _mysql_table_options(),
    )

    incident_id: Mapped[str] = mapped_column(
        ForeignKey("operational_incidents.id", ondelete="CASCADE"), primary_key=True
    )
    alert_id: Mapped[str] = mapped_column(
        ForeignKey("alert_lifecycles.id", ondelete="RESTRICT"), primary_key=True
    )
    incident_rule_version: Mapped[int] = mapped_column(nullable=False)
    first_trigger_window: Mapped[bool] = mapped_column(nullable=False)
    linked_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class OperationalIncidentActivityRow(Base):
    __tablename__ = "operational_incident_activities"
    __table_args__ = (
        CheckConstraint(
            f"kind IN ({OPERATIONAL_INCIDENT_ACTIVITY_VALUES})",
            name="operational_incident_activity_kind",
        ),
        CheckConstraint(
            "actor_type IN ('SYSTEM', 'USER', 'FEISHU')",
            name="operational_incident_activity_actor_type",
        ),
        CheckConstraint(
            "JSON_TYPE(metadata) = 'OBJECT' AND JSON_LENGTH(metadata) <= 20",
            name="operational_incident_activity_metadata",
        ),
        Index(
            "ix_operational_incident_activities_timeline",
            "incident_id",
            "occurred_at",
            "id",
        ),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(37), primary_key=True)
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("operational_incidents.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    activity_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, nullable=False
    )


class IncidentEvaluationJobRow(Base):
    __tablename__ = "incident_evaluation_jobs"
    __table_args__ = (
        UniqueConstraint("alert_id", "alert_version", name="incident_evaluation_alert_version"),
        CheckConstraint(
            f"state IN ({INCIDENT_ASYNC_STATE_VALUES})",
            name="incident_evaluation_job_state",
        ),
        CheckConstraint(
            "alert_version >= 1 AND attempt_count BETWEEN 0 AND 10",
            name="incident_evaluation_job_bounds",
        ),
        Index("ix_incident_evaluation_jobs_claim", "state", "available_at", "created_at"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_id: Mapped[str] = mapped_column(
        ForeignKey("alert_lifecycles.id", ondelete="RESTRICT"), nullable=False
    )
    alert_version: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt_count: Mapped[int] = mapped_column(nullable=False)
    available_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    incident_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class IncidentNotificationRouteRow(Base):
    __tablename__ = "incident_notification_routes"
    __table_args__ = (
        UniqueConstraint(
            "enabled_environment_key",
            name="incident_route_enabled_environment",
        ),
        CheckConstraint(ENVIRONMENT_CHECK, name="incident_notification_route_environment"),
        CheckConstraint("version >= 1", name="incident_notification_route_version"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chat_name: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False)
    enabled_environment_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class IncidentNotificationOutboxRow(Base):
    __tablename__ = "incident_notification_outbox"
    __table_args__ = (
        UniqueConstraint("notification_key", name="incident_notification_key"),
        CheckConstraint(
            f"state IN ({INCIDENT_ASYNC_STATE_VALUES})",
            name="incident_notification_outbox_state",
        ),
        CheckConstraint(
            "kind IN ('CREATE_CARD', 'UPDATE_CARD', 'THREAD_REPLY')",
            name="incident_notification_outbox_kind",
        ),
        CheckConstraint(
            "attempt_count BETWEEN 0 AND 10 AND char_length(notification_key) = 64",
            name="incident_notification_outbox_bounds",
        ),
        Index(
            "ix_incident_notification_outbox_claim",
            "state",
            "available_at",
            "created_at",
        ),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("operational_incidents.id", ondelete="CASCADE"), nullable=False
    )
    activity_id: Mapped[str] = mapped_column(
        String(37),
        ForeignKey("operational_incident_activities.id", ondelete="CASCADE"), nullable=False
    )
    notification_key: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    attempt_count: Mapped[int] = mapped_column(nullable=False)
    available_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feishu_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class IncidentFeishuThreadRow(Base):
    __tablename__ = "incident_feishu_threads"
    __table_args__ = (
        UniqueConstraint("incident_id", name="incident_feishu_thread_incident"),
        UniqueConstraint(
            "chat_id",
            "root_message_id",
            name="incident_feishu_thread_message",
        ),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("operational_incidents.id", ondelete="CASCADE"), nullable=False
    )
    route_id: Mapped[str] = mapped_column(
        ForeignKey("incident_notification_routes.id", ondelete="RESTRICT"), nullable=False
    )
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False)
    root_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    last_synced_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class FeishuEventReceiptRow(Base):
    __tablename__ = "feishu_event_receipts"
    __table_args__ = (
        UniqueConstraint("event_id", name="feishu_event_identity"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    received_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class IncidentRow(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint(
            f"state IN ({INCIDENT_STATE_VALUES})",
            name="incident_state",
        ),
        CheckConstraint(f"severity IN ({SEVERITY_VALUES})", name="incident_severity"),
        CheckConstraint(ENVIRONMENT_CHECK, name="incident_environment"),
        CheckConstraint(
            "(assignee IS NULL AND claimed_at IS NULL) OR "
            "(assignee IS NOT NULL AND claimed_at IS NOT NULL)",
            name="incident_assignment_pair",
        ),
        CheckConstraint(
            "(state NOT IN ('RESOLVED', 'CLOSED') "
            "AND resolved_at IS NULL AND closed_at IS NULL) OR "
            "(state = 'RESOLVED' AND resolved_at IS NOT NULL AND closed_at IS NULL) OR "
            "(state = 'CLOSED' AND resolved_at IS NOT NULL AND closed_at IS NOT NULL)",
            name="incident_resolution_times",
        ),
        Index("ix_incidents_state", "state"),
        Index("ix_incidents_primary_alert_id", "primary_alert_id"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    primary_alert_id: Mapped[str] = mapped_column(
        ForeignKey("alerts.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    service: Mapped[str] = mapped_column(String(128), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    assignee: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    state_changed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)


class AlertGroupRow(Base):
    __tablename__ = "alert_groups"
    __table_args__ = (
        CheckConstraint(f"state IN ({ALERT_GROUP_STATE_VALUES})", name="alert_group_state"),
        CheckConstraint(f"storm_state IN ({STORM_STATE_VALUES})", name="alert_group_storm_state"),
        CheckConstraint(f"severity IN ({SEVERITY_VALUES})", name="alert_group_severity"),
        CheckConstraint(ENVIRONMENT_CHECK, name="alert_group_environment"),
        CheckConstraint(
            "entity_type IN ('SERVICE','WORKLOAD','POD','NODE','JOB',"
            "'INSTANCE','CLUSTER','UNKNOWN')",
            name="alert_group_entity_type",
        ),
        CheckConstraint("char_length(entity_key) = 64", name="alert_group_entity_key"),
        CheckConstraint("char_length(problem_key) = 64", name="alert_group_problem_key"),
        CheckConstraint("char_length(scope_key) = 64", name="alert_group_scope_key"),
        CheckConstraint(
            "scope_type IN ('SERVICE','WORKLOAD','NAMESPACE','CLUSTER','JOB','SOURCE')",
            name="alert_group_scope_type",
        ),
        CheckConstraint(
            "signature_version = 'problem-signature.v1'",
            name="alert_group_signature_version",
        ),
        CheckConstraint(
            "active_count >= 0 AND total_count >= 1 "
            "AND active_count <= total_count AND impacted_resource_count >= 1",
            name="alert_group_counts",
        ),
        CheckConstraint("desired_correlation_version >= 0", name="alert_group_target_version"),
        CheckConstraint(
            "profile_version >= 1 AND member_limit BETWEEN 1 AND 1000 "
            "AND pending_count BETWEEN 0 AND member_limit",
            name="alert_group_event_bounds",
        ),
        CheckConstraint("version >= 1", name="alert_group_version"),
        CheckConstraint(
            "JSON_TYPE(reason_codes) = 'ARRAY' AND JSON_LENGTH(reason_codes) BETWEEN 1 AND 10",
            name="alert_group_reason_count",
        ),
        Index(
            "ix_alert_groups_candidate",
            "state",
            "problem_key",
            "last_observed_at",
        ),
        Index("ix_alert_groups_incident_id", "incident_id"),
        Index("ix_alert_groups_storm_state", "storm_state"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    storm_state: Mapped[str] = mapped_column(String(16), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    service: Mapped[str | None] = mapped_column(String(128), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_display_name: Mapped[str] = mapped_column(String(257), nullable=False)
    problem_key: Mapped[str] = mapped_column(String(64), nullable=False)
    problem_type: Mapped[str] = mapped_column(String(200), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_display_name: Mapped[str] = mapped_column(String(257), nullable=False)
    signature_version: Mapped[str] = mapped_column(String(32), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    symptom: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    representative_alert_id: Mapped[str] = mapped_column(
        ForeignKey("alerts.id", ondelete="RESTRICT"), nullable=False
    )
    incident_id: Mapped[str | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=True
    )
    first_observed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    state_changed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    last_member_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    active_count: Mapped[int] = mapped_column(nullable=False)
    total_count: Mapped[int] = mapped_column(nullable=False)
    impacted_resource_count: Mapped[int] = mapped_column(nullable=False)
    desired_correlation_version: Mapped[int] = mapped_column(nullable=False)
    profile_version: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    forming_until: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    observing_until: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    member_limit: Mapped[int] = mapped_column(nullable=False, default=1000, server_default="1000")
    continuation_group_id: Mapped[str | None] = mapped_column(
        ForeignKey("alert_groups.id", ondelete="RESTRICT"), nullable=True
    )
    pending_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    explanation: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)


class AlertGroupMemberRow(Base):
    __tablename__ = "alert_group_members"
    __table_args__ = (
        UniqueConstraint("alert_id", "alert_cycle", name="alert_group_member_cycle"),
        CheckConstraint("alert_cycle >= 1", name="alert_group_member_cycle_positive"),
        CheckConstraint(
            "joined_alert_version >= 1 AND current_alert_version >= joined_alert_version",
            name="alert_group_member_versions",
        ),
        CheckConstraint(
            "current_state IN ('ACTIVE', 'RESOLVED', 'SUPPRESSED')",
            name="alert_group_member_state",
        ),
        CheckConstraint(
            f"current_severity IN ({SEVERITY_VALUES})", name="alert_group_member_severity"
        ),
        CheckConstraint("char_length(resource_key) = 64", name="alert_group_member_resource_key"),
        Index(
            "ix_alert_group_members_group_state",
            "alert_group_id",
            "current_state",
            "current_severity",
        ),
        Index("ix_alert_group_members_resource", "alert_group_id", "resource_key"),
        _mysql_table_options(),
    )

    alert_group_id: Mapped[str] = mapped_column(
        ForeignKey("alert_groups.id", ondelete="RESTRICT"), primary_key=True
    )
    alert_id: Mapped[str] = mapped_column(
        ForeignKey("alerts.id", ondelete="RESTRICT"), primary_key=True
    )
    alert_cycle: Mapped[int] = mapped_column(primary_key=True)
    joined_alert_version: Mapped[int] = mapped_column(nullable=False)
    current_alert_version: Mapped[int] = mapped_column(nullable=False)
    current_state: Mapped[str] = mapped_column(String(16), nullable=False)
    current_severity: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_name: Mapped[str] = mapped_column(String(512), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class AlertEventProfileRow(Base):
    __tablename__ = "alert_event_profiles"
    __table_args__ = (
        CheckConstraint("profile_version >= 1", name="profile_version"),
        CheckConstraint("JSON_TYPE(services) = 'ARRAY'", name="services_array"),
        CheckConstraint("JSON_TYPE(entity_keys) = 'ARRAY'", name="entity_keys_array"),
        CheckConstraint("JSON_TYPE(scope_types) = 'ARRAY'", name="scope_types_array"),
        CheckConstraint("JSON_TYPE(topology_edges) = 'ARRAY'", name="topology_edges_array"),
        CheckConstraint("JSON_TYPE(problem_types) = 'ARRAY'", name="problem_types_array"),
        CheckConstraint("JSON_TYPE(symptoms) = 'ARRAY'", name="symptoms_array"),
        CheckConstraint(
            "auto_confirmed_count >= 0 AND manual_confirmed_count >= 0 AND pending_count >= 0",
            name="member_counts",
        ),
        Index("ix_alert_event_profiles_group_created", "alert_group_id", "created_at"),
        _mysql_table_options(),
    )

    alert_group_id: Mapped[str] = mapped_column(
        ForeignKey("alert_groups.id", ondelete="RESTRICT"), primary_key=True
    )
    profile_version: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    services: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    entity_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    scope_types: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    topology_edges: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    problem_types: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    symptoms: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    normalized_text: Mapped[str] = mapped_column(String(2048), nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    auto_confirmed_count: Mapped[int] = mapped_column(nullable=False)
    manual_confirmed_count: Mapped[int] = mapped_column(nullable=False)
    pending_count: Mapped[int] = mapped_column(nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class AlertEventMembershipDecisionRow(Base):
    __tablename__ = "alert_event_membership_decisions"
    __table_args__ = (
        CheckConstraint("alert_cycle >= 1 AND alert_version >= 1", name="alert_version"),
        CheckConstraint(f"state IN ({ALERT_GROUP_MEMBERSHIP_STATE_VALUES})", name="state"),
        CheckConstraint(
            "(state = 'PENDING' AND selected_group_id IS NULL AND selected_group_version IS NULL) "
            "OR (state IN ('AUTO_CONFIRMED', 'MANUAL_CONFIRMED', 'REMOVED') "
            "AND selected_group_id IS NOT NULL AND selected_group_version IS NOT NULL)",
            name="selection_pair",
        ),
        CheckConstraint("JSON_TYPE(candidate_group_ids) = 'ARRAY'", name="candidates_array"),
        CheckConstraint("JSON_TYPE(scores) = 'OBJECT'", name="scores_object"),
        CheckConstraint("JSON_TYPE(reason_codes) = 'ARRAY'", name="reasons_array"),
        Index("ix_alert_event_membership_alert", "alert_id", "alert_cycle", "created_at"),
        Index("ix_alert_event_membership_selected", "selected_group_id", "created_at"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(37), primary_key=True)
    alert_id: Mapped[str] = mapped_column(
        ForeignKey("alerts.id", ondelete="RESTRICT"), nullable=False
    )
    alert_cycle: Mapped[int] = mapped_column(nullable=False)
    alert_version: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    candidate_group_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    selected_group_id: Mapped[str | None] = mapped_column(
        ForeignKey("alert_groups.id", ondelete="RESTRICT"), nullable=True
    )
    selected_group_version: Mapped[int | None] = mapped_column(nullable=True)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    scores: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    explanation: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class AlertEventOperationRow(Base):
    __tablename__ = "alert_event_operations"
    __table_args__ = (
        UniqueConstraint("scope", "idempotency_key_hash", name="alert_event_operation_key"),
        CheckConstraint(f"kind IN ({ALERT_EVENT_OPERATION_KIND_VALUES})", name="kind"),
        CheckConstraint(
            "char_length(idempotency_key_hash) = 64 AND char_length(command_fingerprint) = 64",
            name="fingerprints",
        ),
        CheckConstraint("JSON_TYPE(before_versions) = 'OBJECT'", name="before_versions_object"),
        CheckConstraint("JSON_TYPE(after_versions) = 'OBJECT'", name="after_versions_object"),
        CheckConstraint("JSON_TYPE(result) = 'OBJECT'", name="result_object"),
        Index("ix_alert_event_operations_source", "source_group_id", "created_at"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(37), primary_key=True)
    scope: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    command_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    source_group_id: Mapped[str] = mapped_column(
        ForeignKey("alert_groups.id", ondelete="RESTRICT"), nullable=False
    )
    target_group_id: Mapped[str | None] = mapped_column(
        ForeignKey("alert_groups.id", ondelete="RESTRICT"), nullable=True
    )
    before_versions: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    after_versions: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class AlertEventLifecycleJobRow(Base):
    __tablename__ = "alert_event_lifecycle_jobs"
    __table_args__ = (
        UniqueConstraint(
            "alert_group_id", "action", "active_slot", name="alert_event_lifecycle_active"
        ),
        CheckConstraint(f"action IN ({ALERT_EVENT_LIFECYCLE_ACTION_VALUES})", name="action"),
        CheckConstraint(f"state IN ({CORRELATION_JOB_STATE_VALUES})", name="state"),
        CheckConstraint("target_group_version >= 1", name="target_version"),
        CheckConstraint("attempts BETWEEN 0 AND 5", name="attempts"),
        CheckConstraint(
            "(state IN ('PENDING', 'LEASED') AND active_slot = 1) OR "
            "(state IN ('SUCCEEDED', 'FAILED') AND active_slot IS NULL)",
            name="active_slot",
        ),
        CheckConstraint(
            "(state = 'LEASED' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'LEASED' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="lease",
        ),
        Index("ix_alert_event_lifecycle_claim", "state", "available_at", "created_at"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(37), primary_key=True)
    alert_group_id: Mapped[str] = mapped_column(
        ForeignKey("alert_groups.id", ondelete="RESTRICT"), nullable=False
    )
    target_group_version: Mapped[int] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    active_slot: Mapped[int | None] = mapped_column(nullable=True)
    attempts: Mapped[int] = mapped_column(nullable=False)
    available_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class AlertGroupingJobRow(Base):
    __tablename__ = "alert_grouping_jobs"
    __table_args__ = (
        UniqueConstraint("alert_id", "alert_version", name="alert_grouping_job_alert_version"),
        CheckConstraint(
            f"state IN ({CORRELATION_JOB_STATE_VALUES})", name="alert_grouping_job_state"
        ),
        CheckConstraint(
            "alert_cycle >= 1 AND alert_version >= 1",
            name="alert_grouping_job_alert_version_positive",
        ),
        CheckConstraint("attempts BETWEEN 0 AND 5", name="alert_grouping_job_attempts"),
        CheckConstraint(
            "(state = 'LEASED' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'LEASED' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="alert_grouping_job_lease",
        ),
        Index("ix_alert_grouping_jobs_claim", "state", "available_at", "created_at"),
        Index("ix_alert_grouping_jobs_alert_id", "alert_id"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_id: Mapped[str] = mapped_column(
        ForeignKey("alerts.id", ondelete="RESTRICT"), nullable=False
    )
    alert_cycle: Mapped[int] = mapped_column(nullable=False)
    alert_version: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(nullable=False)
    available_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class AlertGroupCorrelationJobRow(Base):
    __tablename__ = "alert_group_correlation_jobs"
    __table_args__ = (
        UniqueConstraint(
            "alert_group_id", "active_slot", name="alert_group_correlation_job_active"
        ),
        CheckConstraint(
            f"state IN ({CORRELATION_JOB_STATE_VALUES})",
            name="job_state",
        ),
        CheckConstraint("target_group_version >= 1", name="target_version"),
        CheckConstraint("attempts BETWEEN 0 AND 5", name="attempts"),
        CheckConstraint(
            "(state IN ('PENDING', 'LEASED') AND active_slot = 1) OR "
            "(state IN ('SUCCEEDED', 'FAILED') AND active_slot IS NULL)",
            name="active_slot",
        ),
        CheckConstraint(
            "(state = 'LEASED' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'LEASED' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="lease",
        ),
        Index("ix_alert_group_correlation_jobs_claim", "state", "available_at", "created_at"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_group_id: Mapped[str] = mapped_column(
        ForeignKey("alert_groups.id", ondelete="RESTRICT"), nullable=False
    )
    target_group_version: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    active_slot: Mapped[int | None] = mapped_column(nullable=True)
    attempts: Mapped[int] = mapped_column(nullable=False)
    available_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class AlertGroupDecisionRow(Base):
    __tablename__ = "alert_group_decisions"
    __table_args__ = (
        UniqueConstraint("job_id", name="alert_group_decision_job"),
        CheckConstraint("group_version >= 1", name="alert_group_decision_group_version"),
        CheckConstraint(
            f"outcome IN ({CORRELATION_OUTCOME_VALUES})", name="alert_group_decision_outcome"
        ),
        CheckConstraint(
            "JSON_TYPE(reason_codes) = 'ARRAY' AND JSON_LENGTH(reason_codes) BETWEEN 1 AND 10",
            name="alert_group_decision_reason_count",
        ),
        CheckConstraint("JSON_TYPE(facts) = 'OBJECT'", name="alert_group_decision_facts_object"),
        CheckConstraint(
            "JSON_TYPE(candidate_incident_ids) = 'ARRAY' "
            "AND JSON_LENGTH(candidate_incident_ids) <= 20",
            name="alert_group_decision_candidate_count",
        ),
        Index("ix_alert_group_decisions_group_id", "alert_group_id"),
        Index("ix_alert_group_decisions_incident_id", "incident_id"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(37), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("alert_group_correlation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    alert_group_id: Mapped[str] = mapped_column(
        ForeignKey("alert_groups.id", ondelete="RESTRICT"), nullable=False
    )
    group_version: Mapped[int] = mapped_column(nullable=False)
    incident_id: Mapped[str | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=True
    )
    outcome: Mapped[str] = mapped_column(String(48), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    facts: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    candidate_incident_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    explanation: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class IncidentActivityRow(Base):
    __tablename__ = "incident_activities"
    __table_args__ = (
        CheckConstraint(
            f"kind IN ({INCIDENT_ACTIVITY_KIND_VALUES})",
            name="incident_activity_kind",
        ),
        CheckConstraint(
            f"from_state IS NULL OR from_state IN ({INCIDENT_STATE_VALUES})",
            name="incident_activity_from_state",
        ),
        CheckConstraint(
            f"to_state IS NULL OR to_state IN ({INCIDENT_STATE_VALUES})",
            name="incident_activity_to_state",
        ),
        CheckConstraint(
            f"note_category IS NULL OR note_category IN ({INCIDENT_NOTE_CATEGORY_VALUES})",
            name="incident_activity_note_category",
        ),
        CheckConstraint(
            "resolution_category IS NULL OR resolution_category IN "
            f"({INCIDENT_RESOLUTION_CATEGORY_VALUES})",
            name="incident_activity_resolution_category",
        ),
        CheckConstraint("incident_version >= 1", name="incident_activity_version"),
        Index(
            "ix_incident_activities_incident_timeline",
            "incident_id",
            "created_at",
            "id",
        ),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(37), primary_key=True)
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str | None] = mapped_column(String(2_000), nullable=True)
    resolution_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution_actions: Mapped[str | None] = mapped_column(String(4_000), nullable=True)
    root_cause: Mapped[str | None] = mapped_column(String(4_000), nullable=True)
    incident_version: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class IncidentOperationRow(Base):
    __tablename__ = "incident_operations"
    __table_args__ = (
        UniqueConstraint("scope", "idempotency_key_hash", name="incident_operation_scope_key"),
        UniqueConstraint("activity_id", name="incident_operation_activity"),
        CheckConstraint(
            "char_length(idempotency_key_hash) = 64",
            name="incident_operation_key_hash",
        ),
        CheckConstraint(
            "char_length(command_fingerprint) = 64",
            name="incident_operation_fingerprint",
        ),
        CheckConstraint(
            f"action IN ({INCIDENT_OPERATION_KIND_VALUES})",
            name="incident_operation_action",
        ),
        CheckConstraint(
            f"result_state IN ({INCIDENT_STATE_VALUES})",
            name="incident_operation_result_state",
        ),
        CheckConstraint("result_version >= 1", name="incident_operation_result_version"),
        Index("ix_incident_operations_incident_id", "incident_id"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    command_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    result_state: Mapped[str] = mapped_column(String(32), nullable=False)
    result_assignee: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result_version: Mapped[int] = mapped_column(nullable=False)
    activity_id: Mapped[str] = mapped_column(
        ForeignKey("incident_activities.id", ondelete="RESTRICT"), nullable=False
    )
    completed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class DiagnosisRunRow(Base):
    __tablename__ = "diagnosis_runs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('QUEUED', 'COLLECTING', 'NORMALIZING', 'SNAPSHOT_READY', "
            "'ANALYZING', 'REPORT_READY', 'PARTIAL', 'FAILED', 'REVIEW_REQUIRED')",
            name="diagnosis_state",
        ),
        CheckConstraint("incident_context_version >= 1", name="diagnosis_context_version"),
        Index("ix_diagnosis_runs_state", "state"),
        Index("ix_diagnosis_runs_incident_id", "incident_id"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(37), primary_key=True)
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    incident_context_version: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)


class IngestionKeyRow(Base):
    __tablename__ = "ingestion_keys"
    __table_args__ = (_mysql_table_options(),)

    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    alert_id: Mapped[str] = mapped_column(String(36), nullable=False)
    incident_id: Mapped[str] = mapped_column(String(36), nullable=False)
    diagnosis_run_id: Mapped[str] = mapped_column(String(37), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class SignalIntakeResultRow(Base):
    __tablename__ = "signal_intake_results"
    __table_args__ = (
        CheckConstraint("char_length(source_event_id) = 64", name="intake_source_event_id"),
        CheckConstraint("char_length(command_fingerprint) = 64", name="intake_command_fingerprint"),
        CheckConstraint(
            f"outcome IN ({PROJECTION_OUTCOME_VALUES})", name="intake_projection_outcome"
        ),
        Index("ix_signal_intake_results_signal_event_id", "signal_event_id"),
        Index("ix_signal_intake_results_alert_id", "alert_id"),
        Index("ix_signal_intake_results_alert_lifecycle_id", "alert_lifecycle_id"),
        _mysql_table_options(),
    )

    alert_source_id: Mapped[str] = mapped_column(
        ForeignKey("alert_sources.id", ondelete="RESTRICT"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    command_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_event_id: Mapped[str] = mapped_column(
        ForeignKey("signal_events.id", ondelete="RESTRICT"), nullable=False
    )
    alert_id: Mapped[str | None] = mapped_column(
        ForeignKey("alerts.id", ondelete="RESTRICT"), nullable=True
    )
    alert_lifecycle_id: Mapped[str | None] = mapped_column(
        ForeignKey("alert_lifecycles.id", ondelete="RESTRICT"), nullable=True
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_resource", "resource_type", "resource_id"),
        Index("ix_audit_events_created_at", "created_at"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(37), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class ServiceCatalogEntryRow(Base):
    __tablename__ = "service_catalog_entries"
    __table_args__ = (
        UniqueConstraint("service", "environment", name="service_catalog_identity"),
        CheckConstraint(f"state IN ({CATALOG_STATE_VALUES})", name="catalog_state"),
        CheckConstraint(
            ENVIRONMENT_CHECK,
            name="catalog_environment",
        ),
        CheckConstraint("char_length(service) >= 1", name="catalog_service_not_empty"),
        CheckConstraint("char_length(owner_team) >= 1", name="catalog_owner_not_empty"),
        CheckConstraint("version >= 1", name="catalog_version"),
        Index("ix_service_catalog_entries_state", "state"),
        Index("ix_service_catalog_entries_environment", "environment"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    service: Mapped[str] = mapped_column(String(128), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_team: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)


class ServiceCatalogStateRow(Base):
    __tablename__ = "service_catalog_state"
    __table_args__ = (
        CheckConstraint("id = 'global'", name="catalog_state_singleton"),
        CheckConstraint("graph_version >= 1", name="catalog_graph_version"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    graph_version: Mapped[int] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class ServiceDependencyRow(Base):
    __tablename__ = "service_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "caller_service_id",
            "dependency_service_id",
            name="service_dependency_edge",
        ),
        CheckConstraint(
            "caller_service_id <> dependency_service_id",
            name="dependency_not_self",
        ),
        CheckConstraint(f"state IN ({CATALOG_STATE_VALUES})", name="dependency_state"),
        CheckConstraint("version >= 1", name="dependency_version"),
        Index("ix_service_dependencies_state", "state"),
        Index("ix_service_dependencies_dependency", "dependency_service_id"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    caller_service_id: Mapped[str] = mapped_column(
        ForeignKey("service_catalog_entries.id", ondelete="RESTRICT"),
        nullable=False,
    )
    dependency_service_id: Mapped[str] = mapped_column(
        ForeignKey("service_catalog_entries.id", ondelete="RESTRICT"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)


class CorrelationJobRow(Base):
    __tablename__ = "correlation_jobs"
    __table_args__ = (
        UniqueConstraint("alert_id", "alert_version", name="correlation_job_alert_version"),
        CheckConstraint(
            f"state IN ({CORRELATION_JOB_STATE_VALUES})",
            name="correlation_job_state",
        ),
        CheckConstraint("alert_version >= 1", name="correlation_job_alert_version_positive"),
        CheckConstraint("attempts BETWEEN 0 AND 5", name="correlation_job_attempts"),
        CheckConstraint(
            "(state = 'LEASED' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'LEASED' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL)",
            name="correlation_job_lease",
        ),
        Index("ix_correlation_jobs_claim", "state", "available_at", "created_at"),
        Index("ix_correlation_jobs_alert_id", "alert_id"),
        Index("ix_correlation_jobs_alert_source_id", "alert_source_id"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(37), primary_key=True)
    alert_source_id: Mapped[str] = mapped_column(
        ForeignKey("alert_sources.id", ondelete="RESTRICT"), nullable=False
    )
    alert_id: Mapped[str] = mapped_column(
        ForeignKey("alerts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    alert_version: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(nullable=False)
    available_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class CorrelationDecisionRow(Base):
    __tablename__ = "correlation_decisions"
    __table_args__ = (
        UniqueConstraint("job_id", name="correlation_decision_job"),
        CheckConstraint("alert_version >= 1", name="correlation_decision_alert_version"),
        CheckConstraint(
            f"outcome IN ({CORRELATION_OUTCOME_VALUES})",
            name="correlation_decision_outcome",
        ),
        CheckConstraint(
            "JSON_TYPE(reason_codes) = 'ARRAY' AND JSON_LENGTH(reason_codes) BETWEEN 1 AND 10",
            name="correlation_decision_reason_count",
        ),
        CheckConstraint(
            "JSON_TYPE(facts) = 'OBJECT'",
            name="correlation_decision_facts_object",
        ),
        CheckConstraint(
            "JSON_TYPE(candidate_incident_ids) = 'ARRAY' "
            "AND JSON_LENGTH(candidate_incident_ids) <= 20",
            name="correlation_decision_candidate_count",
        ),
        Index("ix_correlation_decisions_alert_id", "alert_id"),
        Index("ix_correlation_decisions_incident_id", "incident_id"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(37), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("correlation_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    alert_id: Mapped[str] = mapped_column(
        ForeignKey("alerts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    alert_version: Mapped[int] = mapped_column(nullable=False)
    incident_id: Mapped[str | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    outcome: Mapped[str] = mapped_column(String(48), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    facts: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    candidate_incident_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    explanation: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class IncidentAlertLinkRow(Base):
    __tablename__ = "incident_alert_links"
    __table_args__ = (
        UniqueConstraint("alert_id", name="incident_alert_link_alert"),
        UniqueConstraint("decision_id", name="incident_alert_link_decision"),
        CheckConstraint("relation IN ('PRIMARY', 'RELATED')", name="incident_alert_relation"),
        Index("ix_incident_alert_links_alert_id", "alert_id"),
        _mysql_table_options(),
    )

    incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    alert_id: Mapped[str] = mapped_column(
        ForeignKey("alerts.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    relation: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("correlation_decisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    group_decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("alert_group_decisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    linked_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
