from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from incident_intelligence.adapters.cloudevents import (
    BinaryCloudEventContext,
    CloudEventData,
    StructuredCloudEvent,
    binary_to_signal_command,
    structured_to_signal_command,
)
from incident_intelligence.adapters.common import AdapterValidationError
from incident_intelligence.domain.forbidden_identity import ForbiddenIdentityError

NOW = datetime(2026, 8, 25, 8, 5, tzinfo=UTC)
EVENT_DATA = {
    "alert_key": "payment-error-rate",
    "title": "支付接口错误率升高",
    "summary": "支付接口错误率超过阈值",
    "severity": "high",
    "service": "payment-api",
    "environment": "production",
    "status": "firing",
    "started_at": "2026-08-25T08:00:00Z",
    "labels": {"region": "cn-east-1", "component": "checkout"},
}
STRUCTURED_EVENT = {
    "specversion": "1.0",
    "id": "event-1",
    "source": "HTTPS://Events.EXAMPLE.com/monitor-a?token=secret#fragment",
    "type": "com.incidentintelligence.alert.v1",
    "subject": "payment-api",
    "time": "2026-08-25T08:04:00Z",
    "datacontenttype": "application/json",
    "data": EVENT_DATA,
}
BINARY_CONTEXT = {
    "specversion": "1.0",
    "id": "event-1",
    "source": "HTTPS://Events.EXAMPLE.com/monitor-a?token=secret#fragment",
    "type": "com.incidentintelligence.alert.v1",
    "subject": "payment-api",
    "time": "2026-08-25T08:04:00Z",
    "datacontenttype": "application/json",
}


def _structured(payload: dict[str, object] | None = None) -> StructuredCloudEvent:
    return StructuredCloudEvent.model_validate(payload or STRUCTURED_EVENT)


def _binary_context(payload: dict[str, object] | None = None) -> BinaryCloudEventContext:
    return BinaryCloudEventContext.model_validate(payload or BINARY_CONTEXT)


def _data(payload: dict[str, object] | None = None) -> CloudEventData:
    return CloudEventData.model_validate(payload or EVENT_DATA)


def test_structured_and_binary_modes_produce_the_same_bounded_command() -> None:
    structured = structured_to_signal_command(_structured(), NOW)
    binary = binary_to_signal_command(_binary_context(), _data(), NOW)

    assert structured == binary
    assert structured.source == "cloudevents"
    assert structured.source_alert_key == "payment-error-rate"
    assert structured.event_type == "alert.firing"
    assert structured.event_at == datetime(2026, 8, 25, 8, 4, tzinfo=UTC)
    assert structured.episode_started_at == datetime(2026, 8, 25, 8, 0, tzinfo=UTC)
    assert structured.title == "支付接口错误率升高"
    assert structured.summary == "支付接口错误率超过阈值"
    assert structured.severity == "high"
    assert structured.service == "payment-api"
    assert structured.environment == "production"
    assert structured.facts == {"region": "cn-east-1", "component": "checkout"}
    assert len(structured.source_instance) == 64
    assert len(structured.source_event_id) == 64
    assert "Events.EXAMPLE.com" not in structured.model_dump_json()
    assert "token=secret" not in structured.model_dump_json()


def test_resolved_event_maps_to_resolved_command() -> None:
    payload = deepcopy(STRUCTURED_EVENT)
    payload["data"]["status"] = "resolved"  # type: ignore[index]

    command = structured_to_signal_command(_structured(payload), NOW)

    assert command.event_type == "alert.resolved"
    assert command.event_at == datetime(2026, 8, 25, 8, 4, tzinfo=UTC)


def test_source_and_id_form_the_stable_event_identity() -> None:
    same_identity_changed_data = deepcopy(STRUCTURED_EVENT)
    same_identity_changed_data["data"]["summary"] = "另一份规范化内容"  # type: ignore[index]
    other_source = deepcopy(STRUCTURED_EVENT)
    other_source["source"] = "https://events.example.com/monitor-b"

    original = structured_to_signal_command(_structured(), NOW)
    changed = structured_to_signal_command(_structured(same_identity_changed_data), NOW)
    isolated = structured_to_signal_command(_structured(other_source), NOW)

    assert changed.source_event_id == original.source_event_id
    assert changed.summary != original.summary
    assert isolated.source_event_id != original.source_event_id
    assert isolated.source_instance != original.source_instance


def test_source_query_fragment_and_json_mode_do_not_change_identity() -> None:
    changed = deepcopy(STRUCTURED_EVENT)
    changed["source"] = "https://events.example.com/monitor-a?another=private#other"

    original = structured_to_signal_command(_structured(), NOW)
    normalized = structured_to_signal_command(_structured(changed), NOW)

    assert normalized.source_instance == original.source_instance
    assert normalized.source_event_id == original.source_event_id


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("specversion", "0.3"),
        ("type", "com.example.unsupported.v1"),
        ("datacontenttype", "text/plain"),
    ],
)
def test_only_the_approved_cloudevent_contract_is_accepted(field: str, value: str) -> None:
    payload = {**STRUCTURED_EVENT, field: value}

    with pytest.raises(ValidationError):
        _structured(payload)


def test_extensions_unknown_data_and_subject_mismatch_are_rejected() -> None:
    extension = {**STRUCTURED_EVENT, "traceparent": "private-trace"}
    with pytest.raises(ValidationError):
        _structured(extension)

    unknown_data = deepcopy(STRUCTURED_EVENT)
    unknown_data["data"]["query"] = "private-query"  # type: ignore[index]
    with pytest.raises(ValidationError):
        _structured(unknown_data)

    wrong_subject = {**STRUCTURED_EVENT, "subject": "another-service"}
    with pytest.raises(AdapterValidationError) as error:
        structured_to_signal_command(_structured(wrong_subject), NOW)
    assert error.value.reason_code == "subject_service_mismatch"


def test_event_time_is_bounded_and_episode_cannot_start_after_event() -> None:
    future = {**STRUCTURED_EVENT, "time": (NOW + timedelta(minutes=6)).isoformat()}
    with pytest.raises(AdapterValidationError) as error:
        structured_to_signal_command(_structured(future), NOW)
    assert error.value.reason_code == "event_time_in_future"

    invalid_order = deepcopy(STRUCTURED_EVENT)
    invalid_order["data"]["started_at"] = "2026-08-25T08:04:01Z"  # type: ignore[index]
    with pytest.raises(ValidationError):
        structured_to_signal_command(_structured(invalid_order), NOW)


def test_required_fields_and_labels_are_bounded() -> None:
    missing_id = deepcopy(STRUCTURED_EVENT)
    del missing_id["id"]
    with pytest.raises(ValidationError):
        _structured(missing_id)

    too_many_labels = deepcopy(EVENT_DATA)
    too_many_labels["labels"] = {f"label-{index}": "value" for index in range(21)}
    with pytest.raises(ValidationError):
        _data(too_many_labels)

    naive_time = {**STRUCTURED_EVENT, "time": "2026-08-25T08:04:00"}
    with pytest.raises(ValidationError):
        _structured(naive_time)


def test_forbidden_identity_is_rejected_at_nested_label_depth() -> None:
    payload = deepcopy(STRUCTURED_EVENT)
    payload["data"]["labels"]["experiment_id"] = "hidden"  # type: ignore[index]

    with pytest.raises(ForbiddenIdentityError):
        structured_to_signal_command(_structured(payload), NOW)


def test_binary_context_can_be_built_from_case_insensitive_http_headers() -> None:
    context = BinaryCloudEventContext.from_headers(
        {
            "Ce-Specversion": "1.0",
            "CE-ID": "event-1",
            "ce-source": "https://events.example.com/monitor-a",
            "ce-type": "com.incidentintelligence.alert.v1",
            "ce-subject": "payment-api",
            "ce-time": "2026-08-25T08:04:00Z",
            "Content-Type": "application/json; charset=utf-8",
        }
    )

    assert context == BinaryCloudEventContext.model_validate(
        {**BINARY_CONTEXT, "source": "https://events.example.com/monitor-a"}
    )


def test_binary_context_rejects_missing_headers_and_extension_attributes() -> None:
    missing_time = {
        "ce-specversion": "1.0",
        "ce-id": "event-1",
        "ce-source": "https://events.example.com/monitor-a",
        "ce-type": "com.incidentintelligence.alert.v1",
        "content-type": "application/json",
    }
    with pytest.raises(ValidationError):
        BinaryCloudEventContext.from_headers(missing_time)

    extension = {**missing_time, "ce-time": "2026-08-25T08:04:00Z", "ce-tenant": "private"}
    with pytest.raises(AdapterValidationError) as error:
        BinaryCloudEventContext.from_headers(extension)
    assert error.value.reason_code == "unsupported_extension_attribute"
