from datetime import datetime

from sqlalchemy.orm import Session

from incident_intelligence.domain.models import Alert, DiagnosisRun, Incident, SignalEvent
from incident_intelligence.persistence.models import (
    AlertRow,
    AuditEventRow,
    DiagnosisRunRow,
    IncidentRow,
    IngestionKeyRow,
    SignalEventRow,
)


class RecordRepositories:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_ingestion(self, scope: str, idempotency_key: str) -> IngestionKeyRow | None:
        return self._session.get(IngestionKeyRow, (scope, idempotency_key))

    def add_signal(self, signal: SignalEvent) -> None:
        self._session.add(
            SignalEventRow(
                id=signal.id,
                source=signal.source,
                source_event_id=signal.source_event_id,
                title=signal.title,
                summary=signal.summary,
                severity=signal.severity,
                service=signal.service,
                environment=signal.environment,
                observed_at=signal.observed_at,
                received_at=signal.received_at,
                facts=signal.facts,
                payload_fingerprint=signal.payload_fingerprint,
                created_at=signal.created_at,
                version=signal.version,
            )
        )
        self._session.flush()

    def add_alert(self, alert: Alert) -> None:
        self._session.add(
            AlertRow(
                id=alert.id,
                signal_event_id=alert.signal_event_id,
                state=alert.state.value,
                title=alert.title,
                severity=alert.severity,
                service=alert.service,
                environment=alert.environment,
                first_observed_at=alert.first_observed_at,
                last_observed_at=alert.last_observed_at,
                created_at=alert.created_at,
                version=alert.version,
            )
        )
        self._session.flush()

    def add_incident(self, incident: Incident) -> None:
        self._session.add(
            IncidentRow(
                id=incident.id,
                primary_alert_id=incident.primary_alert_id,
                state=incident.state.value,
                title=incident.title,
                severity=incident.severity,
                service=incident.service,
                environment=incident.environment,
                detected_at=incident.detected_at,
                created_at=incident.created_at,
                version=incident.version,
            )
        )
        self._session.flush()

    def add_diagnosis(self, diagnosis: DiagnosisRun) -> None:
        self._session.add(
            DiagnosisRunRow(
                id=diagnosis.id,
                incident_id=diagnosis.incident_id,
                incident_context_version=diagnosis.incident_context_version,
                state=diagnosis.state.value,
                created_at=diagnosis.created_at,
                version=diagnosis.version,
            )
        )
        self._session.flush()

    def add_ingestion(
        self,
        *,
        scope: str,
        idempotency_key: str,
        payload_fingerprint: str,
        signal_event_id: str,
        alert_id: str,
        incident_id: str,
        diagnosis_run_id: str,
        created_at: datetime,
    ) -> None:
        self._session.add(
            IngestionKeyRow(
                scope=scope,
                idempotency_key=idempotency_key,
                payload_fingerprint=payload_fingerprint,
                signal_event_id=signal_event_id,
                alert_id=alert_id,
                incident_id=incident_id,
                diagnosis_run_id=diagnosis_run_id,
                created_at=created_at,
            )
        )
        self._session.flush()

    def add_audit(
        self,
        *,
        audit_id: str,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
        details: dict[str, str],
        created_at: datetime,
    ) -> None:
        self._session.add(
            AuditEventRow(
                id=audit_id,
                actor=actor,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=request_id,
                details=details,
                created_at=created_at,
            )
        )
