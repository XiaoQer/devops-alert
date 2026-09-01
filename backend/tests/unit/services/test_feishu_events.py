from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from incident_intelligence.services.feishu_events import (
    FeishuCallbackRejected,
    FeishuEventService,
)

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
TOKEN = "verification-token"


def test_valid_thread_mention_records_plain_text_once() -> None:
    receipts = FakeReceiptRepository()
    incidents = FakeIncidentRepository()
    service = _service(receipts, incidents)
    body = _event_body(text="  @_user_1 已联系数据库同学\n等待确认  ")
    raw = _raw(body)

    first = service.handle_event(_headers(raw), raw)
    replay = service.handle_event(_headers(raw), raw)

    assert first.outcome == "RECORDED"
    assert replay.outcome == "REPLAYED"
    assert len(incidents.activities) == 1
    assert incidents.activities[0].summary == "已联系数据库同学\n等待确认"
    assert "open_user_1" not in incidents.activities[0].actor


def test_url_verification_uses_verification_token_without_signature_headers() -> None:
    service = _service(FakeReceiptRepository(), FakeIncidentRepository())
    raw = _raw(
        {
            "type": "url_verification",
            "token": TOKEN,
            "challenge": "challenge-value",
        }
    )

    result = service.handle_event({}, raw)

    assert result.outcome == "CHALLENGE"
    assert result.challenge == "challenge-value"


@pytest.mark.parametrize("mutation", ("wrong_chat", "not_thread", "without_mention", "bot"))
def test_untrusted_or_irrelevant_message_is_ignored(mutation: str) -> None:
    receipts = FakeReceiptRepository()
    incidents = FakeIncidentRepository()
    service = _service(receipts, incidents)
    body = _event_body()
    if mutation == "wrong_chat":
        body["event"]["message"]["chat_id"] = "oc_unknown"
    elif mutation == "not_thread":
        body["event"]["message"]["root_id"] = ""
    elif mutation == "without_mention":
        body["event"]["mentions"] = []
    else:
        body["event"]["sender"]["sender_type"] = "app"
    raw = _raw(body)

    result = service.handle_event(_headers(raw), raw)

    assert result.outcome == "IGNORED"
    assert incidents.activities == []


def test_forged_or_stale_callback_is_rejected_before_database_access() -> None:
    receipts = FakeReceiptRepository()
    service = _service(receipts, FakeIncidentRepository())
    raw = _raw(_event_body())

    with pytest.raises(FeishuCallbackRejected) as forged:
        service.handle_event({**_headers(raw), "x-lark-signature": "forged"}, raw)
    stale_headers = _headers(raw)
    stale_headers["x-lark-request-timestamp"] = str(int(NOW.timestamp()) - 301)
    stale_headers["x-lark-signature"] = _signature(raw, stale_headers)
    with pytest.raises(FeishuCallbackRejected) as stale:
        service.handle_event(stale_headers, raw)

    assert forged.value.error_code == "invalid_signature"
    assert stale.value.error_code == "stale_callback"
    assert receipts.events == {}


def test_ack_card_action_uses_incident_state_machine() -> None:
    incident_service = FakeIncidentService()
    service = _service(
        FakeReceiptRepository(),
        FakeIncidentRepository(),
        incident_service=incident_service,
    )
    raw = _raw(_action_body("ACKNOWLEDGE"))

    result = service.handle_card_action(_headers(raw), raw)

    assert result.outcome == "ACKNOWLEDGED"
    assert incident_service.calls == [
        ("ACKNOWLEDGE", "inc_11111111111111111111111111111111", 1, "evt_action")
    ]


def test_resolve_card_action_requires_resolution_summary() -> None:
    incident_service = FakeIncidentService()
    service = _service(
        FakeReceiptRepository(),
        FakeIncidentRepository(),
        incident_service=incident_service,
    )
    raw = _raw(_action_body("RESOLVE", summary=""))

    result = service.handle_card_action(_headers(raw), raw)

    assert result.outcome == "VALIDATION_ERROR"
    assert incident_service.calls == []


def test_resolve_card_action_uses_trimmed_summary() -> None:
    incident_service = FakeIncidentService()
    service = _service(
        FakeReceiptRepository(),
        FakeIncidentRepository(),
        incident_service=incident_service,
    )
    raw = _raw(_action_body("RESOLVE", summary="  服务已恢复  "))

    result = service.handle_card_action(_headers(raw), raw)

    assert result.outcome == "RESOLVED"
    assert incident_service.calls == [
        ("RESOLVE", "inc_11111111111111111111111111111111", 1, "evt_action")
    ]


@pytest.mark.parametrize(
    ("body", "error_code"),
    (
        (b"x" * 65_537, "callback_too_large"),
        (b"{", "invalid_json"),
        (b"[]", "invalid_envelope"),
        (
            b'{"type":"url_verification","token":"wrong-token","challenge":"value"}',
            "invalid_verification_token",
        ),
    ),
)
def test_invalid_envelopes_are_rejected(body: bytes, error_code: str) -> None:
    service = _service(FakeReceiptRepository(), FakeIncidentRepository())

    with pytest.raises(FeishuCallbackRejected) as captured:
        service.handle_event({}, body)

    assert captured.value.error_code == error_code


def test_damaged_encrypted_callback_is_rejected() -> None:
    service = FeishuEventService(
        uow_factory=lambda: FakeUnitOfWork(
            FakeReceiptRepository(),
            FakeThreadRepository(),
            FakeIncidentRepository(),
        ),
        incident_service=FakeIncidentService(),
        verification_token=TOKEN,
        encrypt_key="encrypt-key",
        clock=lambda: NOW,
    )
    raw = _raw({"encrypt": "not-base64"})

    with pytest.raises(FeishuCallbackRejected) as captured:
        service.handle_event(_headers(raw, key="encrypt-key"), raw)

    assert captured.value.error_code == "invalid_encrypted_callback"


def test_unknown_event_type_is_ignored_once() -> None:
    receipts = FakeReceiptRepository()
    service = _service(receipts, FakeIncidentRepository())
    body = _event_body()
    body["header"]["event_type"] = "application.bot.menu_v6"
    raw = _raw(body)

    first = service.handle_event(_headers(raw), raw)
    second = service.handle_event(_headers(raw), raw)

    assert first.outcome == "IGNORED"
    assert second.outcome == "REPLAYED"


def test_missing_event_id_is_rejected_and_invalid_text_is_ignored() -> None:
    service = _service(FakeReceiptRepository(), FakeIncidentRepository())
    missing_id = _event_body()
    missing_id["header"]["event_id"] = ""
    missing_raw = _raw(missing_id)

    with pytest.raises(FeishuCallbackRejected) as captured:
        service.handle_event(_headers(missing_raw), missing_raw)

    invalid_text = _event_body()
    invalid_text["event"]["message"]["content"] = "not-json"
    invalid_raw = _raw(invalid_text)
    ignored = service.handle_event(_headers(invalid_raw), invalid_raw)
    assert captured.value.error_code == "event_id_required"
    assert ignored.outcome == "IGNORED"


def test_encrypted_event_is_decrypted_after_raw_body_signature_verification() -> None:
    receipts = FakeReceiptRepository()
    incidents = FakeIncidentRepository()
    service = FeishuEventService(
        uow_factory=lambda: FakeUnitOfWork(receipts, FakeThreadRepository(), incidents),
        incident_service=FakeIncidentService(),
        verification_token=TOKEN,
        encrypt_key="encrypt-key",
        clock=lambda: NOW,
        id_factory=lambda prefix: "iact_99999999999999999999999999999999",
    )
    encrypted = _encrypt(_raw(_event_body()), "encrypt-key")
    raw = _raw({"encrypt": encrypted})

    result = service.handle_event(_headers(raw, key="encrypt-key"), raw)

    assert result.outcome == "RECORDED"
    assert len(incidents.activities) == 1


def test_thread_message_keeps_up_to_four_thousand_plain_text_characters() -> None:
    incidents = FakeIncidentRepository()
    service = _service(FakeReceiptRepository(), incidents)
    raw = _raw(_event_body(text="@_user_1 " + "排" * 600))

    service.handle_event(_headers(raw), raw)

    assert incidents.activities[0].summary == "排" * 600


class FakeUnitOfWork:
    def __init__(self, receipts, threads, incidents) -> None:
        self.feishu_event_receipts = receipts
        self.incident_feishu_threads = threads
        self.incidents = incidents

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def commit(self) -> None:
        return None


class FakeReceiptRepository:
    def __init__(self) -> None:
        self.events = {}

    def record_once(self, record) -> bool:
        if record.event_id in self.events:
            return False
        self.events[record.event_id] = record
        return True


class FakeThreadRepository:
    def find_by_message(self, chat_id: str, root_message_id: str):
        if (chat_id, root_message_id) != ("oc_incident", "om_root"):
            return None
        return type("Thread", (), {"incident_id": "inc_11111111111111111111111111111111"})()


class FakeIncidentRepository:
    def __init__(self) -> None:
        self.activities = []

    def append_activities(self, activities) -> None:
        self.activities.extend(activities)


class FakeIncidentService:
    def __init__(self) -> None:
        self.calls = []

    def acknowledge(self, incident_id: str, **kwargs):
        self.calls.append(
            ("ACKNOWLEDGE", incident_id, kwargs["expected_version"], kwargs["idempotency_key"])
        )

    def resolve(self, incident_id: str, **kwargs):
        self.calls.append(
            ("RESOLVE", incident_id, kwargs["expected_version"], kwargs["idempotency_key"])
        )


def _service(receipts, incidents, *, incident_service=None) -> FeishuEventService:
    return FeishuEventService(
        uow_factory=lambda: FakeUnitOfWork(
            receipts,
            FakeThreadRepository(),
            incidents,
        ),
        incident_service=incident_service or FakeIncidentService(),
        verification_token=TOKEN,
        encrypt_key=None,
        clock=lambda: NOW,
        id_factory=lambda prefix: "iact_99999999999999999999999999999999",
    )


def _event_body(text: str = "@_user_1 已开始排查") -> dict:
    return {
        "schema": "2.0",
        "header": {
            "event_id": "evt_message",
            "event_type": "im.message.receive_v1",
            "create_time": str(int(NOW.timestamp() * 1000)),
            "token": TOKEN,
        },
        "event": {
            "sender": {"sender_id": {"open_id": "open_user_1"}, "sender_type": "user"},
            "message": {
                "message_id": "om_reply",
                "root_id": "om_root",
                "parent_id": "om_root",
                "chat_id": "oc_incident",
                "message_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            "mentions": [{"key": "@_user_1", "id": {"open_id": "open_bot"}}],
        },
    }


def _action_body(action: str, *, summary: str | None = None) -> dict:
    value = {
        "action": action,
        "incident_id": "inc_11111111111111111111111111111111",
        "expected_version": 1,
    }
    if summary is not None:
        value["resolution_summary"] = summary
    return {
        "schema": "2.0",
        "header": {
            "event_id": "evt_action",
            "event_type": "card.action.trigger",
            "create_time": str(int(NOW.timestamp() * 1000)),
            "token": TOKEN,
        },
        "event": {
            "operator": {"operator_id": {"open_id": "open_user_1"}},
            "action": {"value": value},
        },
    }


def _raw(body: dict) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()


def _headers(raw: bytes, *, key: str = TOKEN) -> dict[str, str]:
    headers = {
        "x-lark-request-timestamp": str(int(NOW.timestamp())),
        "x-lark-request-nonce": "nonce-1",
    }
    headers["x-lark-signature"] = _signature(raw, headers, key=key)
    return headers


def _signature(raw: bytes, headers: dict[str, str], *, key: str = TOKEN) -> str:
    material = (
        headers["x-lark-request-timestamp"].encode()
        + headers["x-lark-request-nonce"].encode()
        + key.encode()
        + raw
    )
    return sha256(material).hexdigest()


def _encrypt(plaintext: bytes, encrypt_key: str) -> str:
    key = sha256(encrypt_key.encode()).digest()
    iv = b"0123456789abcdef"
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + ciphertext).decode()
