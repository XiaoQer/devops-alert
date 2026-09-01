from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from secrets import compare_digest
from typing import Protocol, cast

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from pydantic import BaseModel, ConfigDict

from incident_intelligence.domain.incidents import IncidentActivity
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.incident_repository import (
    FeishuEventReceiptRecord,
    FeishuEventReceiptRepository,
    IncidentFeishuThreadRepository,
    IncidentRepository,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork


class IncidentActionService(Protocol):
    def acknowledge(
        self,
        incident_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> object: ...

    def resolve(
        self,
        incident_id: str,
        *,
        expected_version: int,
        resolution_summary: str,
        idempotency_key: str,
        actor: str,
        request_id: str,
    ) -> object: ...


class FeishuCallbackResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: str
    challenge: str | None = None


class FeishuCallbackRejected(Exception):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class FeishuEventService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        incident_service: IncidentActionService,
        verification_token: str,
        encrypt_key: str | None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[IdPrefix], str] = new_id,
    ) -> None:
        if not verification_token.strip():
            raise ValueError("verification_token_required")
        self._uow_factory = uow_factory
        self._incident_service = incident_service
        self._verification_token = verification_token
        self._encrypt_key = encrypt_key.strip() if encrypt_key else None
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory

    def handle_event(
        self,
        headers: Mapping[str, str],
        body: bytes,
    ) -> FeishuCallbackResult:
        envelope = self._validated_envelope(headers, body)
        challenge = envelope.get("challenge")
        if envelope.get("type") == "url_verification" and isinstance(challenge, str):
            return FeishuCallbackResult(outcome="CHALLENGE", challenge=challenge[:512])
        header = _mapping(envelope.get("header"))
        event_id = _bounded_string(header.get("event_id"), 128)
        event_type = _bounded_string(header.get("event_type"), 128)
        if not event_id:
            raise FeishuCallbackRejected("event_id_required")
        if event_type != "im.message.receive_v1":
            return self._record_ignored(event_id, event_type or "unknown")

        event = _mapping(envelope.get("event"))
        sender = _mapping(event.get("sender"))
        message = _mapping(event.get("message"))
        chat_id = _bounded_string(message.get("chat_id"), 128)
        root_id = _bounded_string(message.get("root_id"), 128)
        message_id = _bounded_string(message.get("message_id"), 128)
        sender_type = _bounded_string(sender.get("sender_type"), 32).casefold()
        mentions = event.get("mentions")
        if (
            not chat_id
            or not root_id
            or not message_id
            or message.get("message_type") != "text"
            or sender_type in {"app", "bot"}
            or not isinstance(mentions, list)
            or not mentions
        ):
            return self._record_ignored(event_id, event_type)

        text = _message_text(message.get("content"), mentions)
        if not text:
            return self._record_ignored(event_id, event_type)
        now = self._now()
        with self._uow_factory() as uow:
            receipts = _receipts(uow)
            thread = _threads(uow).find_by_message(chat_id, root_id)
            if thread is None:
                if not receipts.record_once(_receipt(event_id, event_type, "IGNORED", now)):
                    return FeishuCallbackResult(outcome="REPLAYED")
                uow.commit()
                return FeishuCallbackResult(outcome="IGNORED")
            if not receipts.record_once(_receipt(event_id, event_type, "RECORDED", now)):
                return FeishuCallbackResult(outcome="REPLAYED")
            sender_id = _bounded_string(
                _mapping(sender.get("sender_id")).get("open_id"),
                128,
            )
            _incidents(uow).append_activities(
                (
                    IncidentActivity(
                        id=self._id_factory("iact"),
                        incident_id=thread.incident_id,
                        kind="FEISHU_MESSAGE_RECORDED",
                        occurred_at=_event_time(header.get("create_time"), now),
                        actor_type="FEISHU",
                        actor=_safe_actor(sender_id),
                        summary=text,
                        metadata={
                            "event_id": event_id,
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "root_message_id": root_id,
                        },
                    ),
                )
            )
            uow.commit()
            return FeishuCallbackResult(outcome="RECORDED")

    def handle_card_action(
        self,
        headers: Mapping[str, str],
        body: bytes,
    ) -> FeishuCallbackResult:
        envelope = self._validated_envelope(headers, body)
        header = _mapping(envelope.get("header"))
        event_id = _bounded_string(header.get("event_id"), 128)
        if not event_id:
            raise FeishuCallbackRejected("event_id_required")
        event = _mapping(envelope.get("event"))
        action = _mapping(_mapping(event.get("action")).get("value"))
        action_name = _bounded_string(action.get("action"), 32)
        incident_id = _bounded_string(action.get("incident_id"), 36)
        expected_version = action.get("expected_version")
        if (
            action_name not in {"ACKNOWLEDGE", "RESOLVE"}
            or not incident_id.startswith("inc_")
            or not isinstance(expected_version, int)
            or expected_version < 1
        ):
            return FeishuCallbackResult(outcome="VALIDATION_ERROR")
        operator_id = _bounded_string(
            _mapping(_mapping(event.get("operator")).get("operator_id")).get("open_id"),
            128,
        )
        actor = _safe_actor(operator_id)
        request_id = f"feishu_{sha256(event_id.encode()).hexdigest()[:24]}"
        if action_name == "ACKNOWLEDGE":
            self._incident_service.acknowledge(
                incident_id,
                expected_version=expected_version,
                idempotency_key=event_id,
                actor=actor,
                request_id=request_id,
            )
            return FeishuCallbackResult(outcome="ACKNOWLEDGED")
        summary = action.get("resolution_summary")
        if not isinstance(summary, str) or not summary.strip():
            return FeishuCallbackResult(outcome="VALIDATION_ERROR")
        self._incident_service.resolve(
            incident_id,
            expected_version=expected_version,
            resolution_summary=summary.strip()[:2_000],
            idempotency_key=event_id,
            actor=actor,
            request_id=request_id,
        )
        return FeishuCallbackResult(outcome="RESOLVED")

    def _validated_envelope(
        self,
        headers: Mapping[str, str],
        body: bytes,
    ) -> dict[str, object]:
        if len(body) > 65_536:
            raise FeishuCallbackRejected("callback_too_large")
        try:
            envelope = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FeishuCallbackRejected("invalid_json") from error
        if not isinstance(envelope, dict):
            raise FeishuCallbackRejected("invalid_envelope")
        if envelope.get("type") == "url_verification":
            self._require_verification_token(envelope)
            return cast(dict[str, object], envelope)
        normalized_headers = {key.casefold(): value for key, value in headers.items()}
        timestamp = normalized_headers.get("x-lark-request-timestamp", "")
        nonce = normalized_headers.get("x-lark-request-nonce", "")
        signature = normalized_headers.get("x-lark-signature", "")
        try:
            callback_time = datetime.fromtimestamp(int(timestamp), tz=UTC)
        except (ValueError, OverflowError) as error:
            raise FeishuCallbackRejected("invalid_timestamp") from error
        if abs((self._now() - callback_time).total_seconds()) > 300:
            raise FeishuCallbackRejected("stale_callback")
        key = self._encrypt_key or self._verification_token
        expected = sha256(timestamp.encode() + nonce.encode() + key.encode() + body).hexdigest()
        if not signature or not compare_digest(signature, expected):
            raise FeishuCallbackRejected("invalid_signature")
        encrypted = envelope.get("encrypt")
        if encrypted is not None:
            if self._encrypt_key is None or not isinstance(encrypted, str):
                raise FeishuCallbackRejected("invalid_encrypted_callback")
            envelope = _decrypt_envelope(encrypted, self._encrypt_key)
        self._require_verification_token(envelope)
        return cast(dict[str, object], envelope)

    def _require_verification_token(self, envelope: Mapping[str, object]) -> None:
        token = _mapping(envelope.get("header")).get("token", envelope.get("token"))
        if not isinstance(token, str) or not compare_digest(token, self._verification_token):
            raise FeishuCallbackRejected("invalid_verification_token")

    def _record_ignored(self, event_id: str, event_type: str) -> FeishuCallbackResult:
        with self._uow_factory() as uow:
            if not _receipts(uow).record_once(
                _receipt(event_id, event_type, "IGNORED", self._now())
            ):
                return FeishuCallbackResult(outcome="REPLAYED")
            uow.commit()
        return FeishuCallbackResult(outcome="IGNORED")

    def _now(self) -> datetime:
        return self._clock().astimezone(UTC)


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _decrypt_envelope(encrypted: str, encrypt_key: str) -> dict[str, object]:
    try:
        ciphertext = base64.b64decode(encrypted, validate=True)
        if len(ciphertext) < 32 or (len(ciphertext) - 16) % 16:
            raise ValueError("invalid_ciphertext_size")
        iv, payload = ciphertext[:16], ciphertext[16:]
        key = sha256(encrypt_key.encode()).digest()
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        padded = decryptor.update(payload) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
        envelope = json.loads(plaintext)
    except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FeishuCallbackRejected("invalid_encrypted_callback") from error
    if not isinstance(envelope, dict):
        raise FeishuCallbackRejected("invalid_envelope")
    return cast(dict[str, object], envelope)


def _bounded_string(value: object, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _message_text(content: object, mentions: list[object]) -> str:
    if not isinstance(content, str) or len(content) > 16_384:
        return ""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return ""
    text = parsed.get("text") if isinstance(parsed, dict) else None
    if not isinstance(text, str):
        return ""
    for mention in mentions:
        key = _mapping(mention).get("key")
        if isinstance(key, str) and key:
            text = text.replace(key, "")
    normalized = "\n".join(line.strip() for line in text.splitlines()).strip()
    return normalized[:4_000]


def _safe_actor(sender_id: str) -> str:
    digest = sha256((sender_id or "unknown").encode()).hexdigest()[:16]
    return f"feishu-user-{digest}"


def _event_time(value: object, fallback: datetime) -> datetime:
    try:
        return datetime.fromtimestamp(int(str(value)) / 1_000, tz=UTC)
    except (ValueError, OverflowError):
        return fallback


def _receipt(
    event_id: str,
    event_type: str,
    outcome: str,
    now: datetime,
) -> FeishuEventReceiptRecord:
    return FeishuEventReceiptRecord(
        id="fer_" + sha256(event_id.encode()).hexdigest()[:32],
        event_id=event_id,
        event_type=event_type[:64] or "unknown",
        outcome=outcome,
        received_at=now,
    )


def _receipts(uow: SqlAlchemyUnitOfWork) -> FeishuEventReceiptRepository:
    if uow.feishu_event_receipts is None:
        raise RuntimeError("工作单元没有可用飞书回执仓储")
    return uow.feishu_event_receipts


def _threads(uow: SqlAlchemyUnitOfWork) -> IncidentFeishuThreadRepository:
    if uow.incident_feishu_threads is None:
        raise RuntimeError("工作单元没有可用飞书线程仓储")
    return uow.incident_feishu_threads


def _incidents(uow: SqlAlchemyUnitOfWork) -> IncidentRepository:
    if uow.incidents is None:
        raise RuntimeError("工作单元没有可用 Incident 仓储")
    return uow.incidents


__all__ = [
    "FeishuCallbackRejected",
    "FeishuCallbackResult",
    "FeishuEventService",
]
