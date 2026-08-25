from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from incident_intelligence.persistence.base import Base
from incident_intelligence.persistence.types import UtcDateTime

SEVERITY_VALUES = "'critical', 'high', 'medium', 'low'"
ENVIRONMENT_VALUES = "'production', 'staging', 'development', 'unknown'"
EVENT_TYPE_VALUES = "'manual.reported', 'alert.firing', 'alert.resolved'"
PROJECTION_OUTCOME_VALUES = (
    "'opened', 'updated', 'resolved', 'reopened', 'stale', 'orphan_resolved'"
)


def _mysql_table_options() -> dict[str, str]:
    return {
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_bin",
    }


class SignalEventRow(Base):
    __tablename__ = "signal_events"
    __table_args__ = (
        UniqueConstraint("source", "source_event_id", name="source_identity"),
        CheckConstraint(f"severity IN ({SEVERITY_VALUES})", name="signal_severity"),
        CheckConstraint(f"environment IN ({ENVIRONMENT_VALUES})", name="signal_environment"),
        CheckConstraint(f"event_type IN ({EVENT_TYPE_VALUES})", name="signal_event_type"),
        Index("ix_signal_events_observed_at", "observed_at"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(String(2_000), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    service: Mapped[str] = mapped_column(String(128), nullable=False)
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
        CheckConstraint(f"severity IN ({SEVERITY_VALUES})", name="alert_severity"),
        CheckConstraint(f"environment IN ({ENVIRONMENT_VALUES})", name="alert_environment"),
        CheckConstraint("char_length(source_instance) = 64", name="alert_source_instance"),
        CheckConstraint("char_length(source_alert_key) >= 1", name="alert_source_alert_key"),
        UniqueConstraint(
            "source", "source_instance", "source_alert_key", name="alert_source_identity"
        ),
        Index("ix_alerts_state", "state"),
        Index("ix_alerts_signal_event_id", "signal_event_id"),
        _mysql_table_options(),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    signal_event_id: Mapped[str] = mapped_column(
        ForeignKey("signal_events.id", ondelete="RESTRICT"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_instance: Mapped[str] = mapped_column(String(64), nullable=False)
    source_alert_key: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    service: Mapped[str] = mapped_column(String(128), nullable=False)
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
            "state IN ('DETECTED', 'TRIAGING', 'INVESTIGATING', 'MITIGATING', "
            "'MONITORING_RECOVERY', 'RESOLVED', 'CLOSED')",
            name="incident_state",
        ),
        CheckConstraint(f"severity IN ({SEVERITY_VALUES})", name="incident_severity"),
        CheckConstraint(f"environment IN ({ENVIRONMENT_VALUES})", name="incident_environment"),
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
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)


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
