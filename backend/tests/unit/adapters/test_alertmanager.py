from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from incident_intelligence.adapters.alertmanager import (
    AlertmanagerWebhook,
    to_signal_commands,
)
from incident_intelligence.adapters.common import (
    AdapterValidationError,
    normalize_source_uri,
)
from incident_intelligence.domain.forbidden_identity import ForbiddenIdentityError

NOW = datetime(2026, 8, 25, 8, 5, tzinfo=UTC)
FIRING_PAYLOAD = {
    "version": "4",
    "groupKey": '{}:{alertname="PaymentHighErrorRate"}',
    "truncatedAlerts": 0,
    "status": "firing",
    "receiver": "incident-intelligence",
    "groupLabels": {"alertname": "PaymentHighErrorRate"},
    "commonLabels": {"service": "payment-api"},
    "commonAnnotations": {},
    "externalURL": "HTTPS://AlertManager.EXAMPLE.com:9093/cluster-a?token=secret#overview",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "PaymentHighErrorRate",
                "service": "payment-api",
                "environment": "production",
                "severity": "high",
                "region": "cn-east-1",
            },
            "annotations": {
                "summary": "支付接口错误率升高",
                "description": "支付接口错误率超过阈值",
            },
            "startsAt": "2026-08-25T08:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "https://prometheus.example.com/graph?g0.expr=secret_query",
            "fingerprint": "fingerprint-1",
        }
    ],
}


def test_alertmanager_firing_maps_to_bounded_signal_command() -> None:
    command = to_signal_commands(AlertmanagerWebhook.model_validate(FIRING_PAYLOAD), NOW)[0]

    assert command.source == "alertmanager"
    assert command.source_alert_key == "fingerprint-1"
    assert command.event_type == "alert.firing"
    assert command.event_at == datetime(2026, 8, 25, 8, 0, tzinfo=UTC)
    assert command.episode_started_at == datetime(2026, 8, 25, 8, 0, tzinfo=UTC)
    assert command.title == "支付接口错误率升高"
    assert command.summary == "支付接口错误率超过阈值"
    assert command.severity == "high"
    assert command.service == "payment-api"
    assert command.environment == "production"
    assert command.facts == {"region": "cn-east-1"}
    assert len(command.source_instance) == 64
    assert len(command.source_event_id) == 64
    assert "generatorURL" not in command.model_dump_json()
    assert "secret_query" not in command.model_dump_json()
    assert "token=secret" not in command.model_dump_json()


def test_alertmanager_keeps_symptom_as_a_bounded_correlation_fact() -> None:
    payload = deepcopy(FIRING_PAYLOAD)
    payload["alerts"][0]["labels"]["symptom"] = "high-error-rate"
    payload["alerts"][0]["labels"]["private_detail"] = "must-not-be-stored"

    command = to_signal_commands(AlertmanagerWebhook.model_validate(payload), NOW)[0]

    assert command.facts == {
        "region": "cn-east-1",
        "symptom": "high-error-rate",
    }
    assert "private_detail" not in command.model_dump_json()
    assert "must-not-be-stored" not in command.model_dump_json()


def test_pod_alert_without_service_is_accepted_with_real_entity() -> None:
    payload = deepcopy(FIRING_PAYLOAD)
    payload["commonLabels"] = {}
    del payload["alerts"][0]["labels"]["service"]
    payload["alerts"][0]["labels"].update({"namespace": "devops-platform", "pod": "aegis-demo-0"})

    command = to_signal_commands(AlertmanagerWebhook.model_validate(payload), NOW)[0]

    assert command.service is None
    assert command.entity_type == "POD"
    assert command.entity_display_name == "devops-platform/aegis-demo-0"


def test_per_alert_labels_override_common_labels() -> None:
    payload = deepcopy(FIRING_PAYLOAD)
    payload["commonLabels"] = {"service": "shared-service", "environment": "staging"}
    payload["alerts"][0]["labels"]["service"] = "payment-api"
    payload["alerts"][0]["labels"]["environment"] = "production"

    command = to_signal_commands(AlertmanagerWebhook.model_validate(payload), NOW)[0]

    assert command.service == "payment-api"
    assert command.environment == "production"


def test_source_uri_is_normalized_without_credentials_query_or_fragment() -> None:
    assert (
        normalize_source_uri(
            "HTTPS://AlertManager.EXAMPLE.com:9093/cluster-a?token=secret#overview"
        )
        == "https://alertmanager.example.com:9093/cluster-a"
    )

    for invalid in (
        "alertmanager.example.com/path",
        "https:///missing-host",
        "https://user:password@alertmanager.example.com/path",
        "https://alertmanager.example.com:invalid/path",
    ):
        with pytest.raises(AdapterValidationError) as error:
            normalize_source_uri(invalid)
        assert error.value.reason_code == "invalid_source_uri"


@pytest.mark.parametrize(
    ("input_value", "expected"),
    [
        ("critical", "critical"),
        ("page", "critical"),
        ("p1", "critical"),
        ("high", "high"),
        ("p2", "high"),
        ("warning", "medium"),
        ("warn", "medium"),
        ("medium", "medium"),
        ("p3", "medium"),
        ("info", "low"),
        ("low", "low"),
        ("p4", "low"),
        ("p5", "low"),
    ],
)
def test_severity_aliases_map_to_fixed_internal_values(
    input_value: str,
    expected: str,
) -> None:
    payload = deepcopy(FIRING_PAYLOAD)
    payload["alerts"][0]["labels"]["severity"] = input_value

    command = to_signal_commands(AlertmanagerWebhook.model_validate(payload), NOW)[0]

    assert command.severity == expected
    assert command.normalization_reason_codes == ()


@pytest.mark.parametrize("input_value", [None, "unknown-level"])
def test_missing_or_unknown_severity_defaults_with_reason_code(
    input_value: str | None,
) -> None:
    payload = deepcopy(FIRING_PAYLOAD)
    if input_value is None:
        del payload["alerts"][0]["labels"]["severity"]
    else:
        payload["alerts"][0]["labels"]["severity"] = input_value

    command = to_signal_commands(AlertmanagerWebhook.model_validate(payload), NOW)[0]

    assert command.severity == "medium"
    assert command.normalization_reason_codes == ("severity_defaulted",)


def test_resolved_alert_uses_end_time() -> None:
    payload = deepcopy(FIRING_PAYLOAD)
    payload["status"] = "resolved"
    payload["alerts"][0]["status"] = "resolved"
    payload["alerts"][0]["endsAt"] = "2026-08-25T08:04:00Z"

    command = to_signal_commands(AlertmanagerWebhook.model_validate(payload), NOW)[0]

    assert command.event_type == "alert.resolved"
    assert command.event_at == datetime(2026, 8, 25, 8, 4, tzinfo=UTC)
    assert command.episode_started_at == datetime(2026, 8, 25, 8, 0, tzinfo=UTC)


def test_resolved_requires_real_end_time_and_events_cannot_be_far_future() -> None:
    resolved = deepcopy(FIRING_PAYLOAD)
    resolved["status"] = "resolved"
    resolved["alerts"][0]["status"] = "resolved"
    with pytest.raises(AdapterValidationError) as missing_end:
        to_signal_commands(AlertmanagerWebhook.model_validate(resolved), NOW)
    assert missing_end.value.reason_code == "missing_resolved_time"

    future = deepcopy(FIRING_PAYLOAD)
    future["alerts"][0]["startsAt"] = "2026-08-25T08:11:00Z"
    with pytest.raises(AdapterValidationError) as future_event:
        to_signal_commands(AlertmanagerWebhook.model_validate(future), NOW)
    assert future_event.value.reason_code == "event_time_in_future"


def test_missing_business_title_uses_safe_fallback_but_fingerprint_remains_required() -> None:
    missing_title = deepcopy(FIRING_PAYLOAD)
    del missing_title["alerts"][0]["labels"]["alertname"]
    del missing_title["alerts"][0]["annotations"]["summary"]
    del missing_title["alerts"][0]["annotations"]["description"]

    command = to_signal_commands(AlertmanagerWebhook.model_validate(missing_title), NOW)[0]

    assert command.title == "未命名告警"
    assert command.summary == "未命名告警"

    missing_fingerprint = deepcopy(FIRING_PAYLOAD)
    missing_fingerprint["alerts"][0]["fingerprint"] = ""
    with pytest.raises(ValidationError):
        AlertmanagerWebhook.model_validate(missing_fingerprint)


def test_unknown_extension_fields_are_ignored_without_being_persisted() -> None:
    payload = deepcopy(FIRING_PAYLOAD)
    payload["futureWebhookField"] = {"arbitrary": "value"}
    payload["alerts"][0]["futureAlertField"] = "value"

    webhook = AlertmanagerWebhook.model_validate(payload)
    command = to_signal_commands(webhook, NOW)[0]

    assert "futureWebhookField" not in webhook.model_dump_json()
    assert "futureAlertField" not in webhook.model_dump_json()
    assert "arbitrary" not in command.model_dump_json()


def test_forbidden_identity_is_scanned_before_unknown_fields_are_ignored() -> None:
    payload = deepcopy(FIRING_PAYLOAD)
    payload["futureWebhookField"] = {"scenario_id": "hidden"}

    with pytest.raises(ForbiddenIdentityError):
        AlertmanagerWebhook.model_validate(payload)


def test_forbidden_identity_is_rejected_from_alert_labels() -> None:
    payload = deepcopy(FIRING_PAYLOAD)
    payload["alerts"][0]["labels"]["scenario_id"] = "hidden"

    with pytest.raises(ForbiddenIdentityError):
        to_signal_commands(AlertmanagerWebhook.model_validate(payload), NOW)


def test_grouping_json_order_and_ignored_urls_do_not_change_event_identity() -> None:
    changed = deepcopy(FIRING_PAYLOAD)
    changed["groupKey"] = "another-group"
    changed["receiver"] = "another-receiver"
    changed["externalURL"] = (
        "https://alertmanager.example.com:9093/cluster-a?different=secret#other"
    )
    changed["alerts"][0]["generatorURL"] = "https://another.example.com/private-query"
    changed["alerts"][0]["labels"] = dict(reversed(list(changed["alerts"][0]["labels"].items())))

    original_command = to_signal_commands(AlertmanagerWebhook.model_validate(FIRING_PAYLOAD), NOW)[
        0
    ]
    changed_command = to_signal_commands(AlertmanagerWebhook.model_validate(changed), NOW)[0]

    assert changed_command.source_instance == original_command.source_instance
    assert changed_command.source_event_id == original_command.source_event_id


def test_normalized_content_change_creates_new_source_event_identity() -> None:
    changed = deepcopy(FIRING_PAYLOAD)
    changed["alerts"][0]["annotations"]["description"] = "错误率和延迟同时超过阈值"

    original_command = to_signal_commands(AlertmanagerWebhook.model_validate(FIRING_PAYLOAD), NOW)[
        0
    ]
    changed_command = to_signal_commands(AlertmanagerWebhook.model_validate(changed), NOW)[0]

    assert changed_command.source_event_id != original_command.source_event_id


def test_webhook_and_nested_fields_are_bounded() -> None:
    too_many_alerts = deepcopy(FIRING_PAYLOAD)
    too_many_alerts["alerts"] = [deepcopy(FIRING_PAYLOAD["alerts"][0]) for _ in range(101)]
    with pytest.raises(ValidationError):
        AlertmanagerWebhook.model_validate(too_many_alerts)

    too_many_labels = deepcopy(FIRING_PAYLOAD)
    too_many_labels["alerts"][0]["labels"].update(
        {f"label_{index}": "value" for index in range(100)}
    )
    with pytest.raises(ValidationError):
        AlertmanagerWebhook.model_validate(too_many_labels)

    too_many_annotations = deepcopy(FIRING_PAYLOAD)
    too_many_annotations["alerts"][0]["annotations"].update(
        {f"note_{index}": "value" for index in range(20)}
    )
    with pytest.raises(ValidationError):
        AlertmanagerWebhook.model_validate(too_many_annotations)
