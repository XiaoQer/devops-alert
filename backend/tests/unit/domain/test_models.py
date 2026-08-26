from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from incident_intelligence.domain.enums import AlertState, DiagnosisState, IncidentState
from incident_intelligence.domain.models import Alert, DiagnosisRun, Incident, SignalEvent
from incident_intelligence.ids import new_id

NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
ALERT_SOURCE_ID = "src_" + "1" * 32


def signal_payload() -> dict[str, object]:
    return {
        "id": new_id("sig"),
        "alert_source_id": ALERT_SOURCE_ID,
        "source": "manual",
        "source_event_id": "manual-001",
        "event_type": "manual.reported",
        "title": "支付接口错误率升高",
        "summary": "支付接口在生产环境持续返回错误",
        "severity": "high",
        "service": "payment-api",
        "environment": "production",
        "observed_at": NOW,
        "received_at": NOW,
        "facts": {"region": "cn-east-1"},
        "payload_fingerprint": "a" * 64,
        "created_at": NOW,
    }


@pytest.mark.parametrize("prefix", ["sig", "alt", "inc", "diag", "aud"])
def test_new_id_uses_expected_prefix_and_uuid_hex(prefix: str) -> None:
    identifier = new_id(prefix)  # type: ignore[arg-type]

    assert re.fullmatch(rf"{prefix}_[0-9a-f]{{32}}", identifier)


def test_signal_event_is_immutable() -> None:
    signal = SignalEvent.model_validate(signal_payload())

    with pytest.raises(ValidationError):
        signal.title = "被修改的标题"


def test_signal_event_preserves_trusted_alert_source_identity() -> None:
    payload = signal_payload()

    signal = SignalEvent.model_validate(payload)

    assert signal.alert_source_id == ALERT_SOURCE_ID


def test_domain_models_keep_state_families_separate() -> None:
    alert = Alert(
        id=new_id("alt"),
        signal_event_id=new_id("sig"),
        alert_source_id=ALERT_SOURCE_ID,
        source="manual",
        source_instance="a" * 64,
        source_alert_key="b" * 64,
        state=AlertState.ACTIVE,
        title="支付接口错误率升高",
        severity="high",
        service="payment-api",
        environment="production",
        first_observed_at=NOW,
        last_observed_at=NOW,
        state_changed_at=NOW,
        created_at=NOW,
    )
    incident = Incident(
        id=new_id("inc"),
        primary_alert_id=alert.id,
        state=IncidentState.DETECTED,
        title=alert.title,
        severity=alert.severity,
        service=alert.service,
        environment=alert.environment,
        detected_at=NOW,
        created_at=NOW,
    )
    diagnosis = DiagnosisRun(
        id=new_id("diag"),
        incident_id=incident.id,
        incident_context_version=1,
        state=DiagnosisState.QUEUED,
        created_at=NOW,
    )

    assert alert.state is AlertState.ACTIVE
    assert alert.alert_source_id == ALERT_SOURCE_ID
    assert incident.state is IncidentState.DETECTED
    assert diagnosis.state is DiagnosisState.QUEUED

    with pytest.raises(ValidationError):
        Incident.model_validate({**incident.model_dump(), "state": "ACTIVE"})


def test_domain_models_reject_naive_timestamps_and_unknown_fields() -> None:
    payload = signal_payload()
    payload["observed_at"] = datetime(2026, 8, 24, 8, 0)
    payload["unexpected"] = "value"

    with pytest.raises(ValidationError) as error:
        SignalEvent.model_validate(payload)

    error_types = {item["type"] for item in error.value.errors()}
    assert "timezone_aware" in error_types
    assert "extra_forbidden" in error_types


def test_domain_models_normalize_aware_timestamps_to_utc() -> None:
    payload = signal_payload()
    payload["observed_at"] = datetime(2026, 8, 24, 16, 0, tzinfo=timezone(timedelta(hours=8)))

    signal = SignalEvent.model_validate(payload)

    assert signal.observed_at == NOW
    assert signal.observed_at.tzinfo is UTC


def test_signal_event_rejects_unknown_event_type() -> None:
    payload = signal_payload()
    payload["event_type"] = "experiment.started"

    with pytest.raises(ValidationError):
        SignalEvent.model_validate(payload)


def test_alert_requires_bounded_normalized_source_identity() -> None:
    payload = {
        "id": new_id("alt"),
        "signal_event_id": new_id("sig"),
        "source": "manual",
        "source_instance": "not-a-digest",
        "source_alert_key": "x" * 129,
        "state": AlertState.ACTIVE,
        "title": "支付接口错误率升高",
        "severity": "high",
        "service": "payment-api",
        "environment": "production",
        "first_observed_at": NOW,
        "last_observed_at": NOW,
        "state_changed_at": NOW,
        "created_at": NOW,
    }

    with pytest.raises(ValidationError) as error:
        Alert.model_validate(payload)

    error_locations = {item["loc"] for item in error.value.errors()}
    assert ("source_instance",) in error_locations
    assert ("source_alert_key",) in error_locations
