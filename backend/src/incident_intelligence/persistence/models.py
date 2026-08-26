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
ENVIRONMENT_VALUES = "'production', 'staging', 'development', 'unknown'"
EVENT_TYPE_VALUES = "'manual.reported', 'alert.firing', 'alert.resolved'"
PROJECTION_OUTCOME_VALUES = (
    "'opened', 'updated', 'resolved', 'reopened', 'stale', 'orphan_resolved'"
)
CATALOG_STATE_VALUES = "'ACTIVE', 'INACTIVE'"
CORRELATION_JOB_STATE_VALUES = "'PENDING', 'LEASED', 'SUCCEEDED', 'FAILED'"
ALERT_GROUP_STATE_VALUES = "'ACTIVE', 'RESOLVED'"
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
        CheckConstraint(f"environment IN ({ENVIRONMENT_VALUES})", name="signal_environment"),
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
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(String(2_000), nullable=False)
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
        CheckConstraint(f"environment IN ({ENVIRONMENT_VALUES})", name="alert_environment"),
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


class IncidentRow(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint(
            f"state IN ({INCIDENT_STATE_VALUES})",
            name="incident_state",
        ),
        CheckConstraint(f"severity IN ({SEVERITY_VALUES})", name="incident_severity"),
        CheckConstraint(f"environment IN ({ENVIRONMENT_VALUES})", name="incident_environment"),
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
        CheckConstraint(f"environment IN ({ENVIRONMENT_VALUES})", name="alert_group_environment"),
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
            f"environment IN ({ENVIRONMENT_VALUES})",
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
