from __future__ import annotations

import base64
import binascii
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe

CAPABILITY_AUDIENCE = "incident-intelligence.diagnosis-tools.v1"
CAPABILITY_VERSION = 1
MAX_CAPABILITY_LIFETIME_SECONDS = 300


@dataclass(frozen=True, slots=True)
class DiagnosisCapability:
    diagnosis_run_id: str
    audience: str
    expires_at: datetime
    nonce: str


@dataclass(frozen=True, slots=True)
class DiagnosisCapabilityDenied(Exception):
    reason_code: str

    def __str__(self) -> str:
        return self.reason_code


class DiagnosisCapabilityIssuer:
    def __init__(
        self,
        *,
        hmac_secret: str,
        clock: Callable[[], datetime] | None = None,
        nonce_factory: Callable[[], str] = lambda: token_urlsafe(18),
    ) -> None:
        if len(hmac_secret) < 16:
            raise ValueError("diagnosis_capability_secret_too_short")
        self._secret = hmac_secret.encode()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._nonce_factory = nonce_factory

    def issue(
        self,
        diagnosis_run_id: str,
        *,
        expires_at: datetime,
    ) -> str:
        now = _as_utc(self._clock())
        expires_at = _as_utc(expires_at)
        lifetime_seconds = int((expires_at - now).total_seconds())
        if not 1 <= lifetime_seconds <= MAX_CAPABILITY_LIFETIME_SECONDS:
            raise ValueError("diagnosis_capability_expiry_invalid")
        payload = {
            "aud": CAPABILITY_AUDIENCE,
            "exp": int(expires_at.timestamp()),
            "nonce": self._nonce_factory(),
            "run_id": diagnosis_run_id,
            "v": CAPABILITY_VERSION,
        }
        encoded_payload = _encode_json(payload)
        signature = hmac.new(self._secret, encoded_payload.encode(), sha256).digest()
        return f"{encoded_payload}.{_encode_bytes(signature)}"

    def verify(self, token: str, *, expected_run_id: str) -> DiagnosisCapability:
        try:
            encoded_payload, encoded_signature = token.split(".", maxsplit=1)
            expected_signature = hmac.new(
                self._secret,
                encoded_payload.encode(),
                sha256,
            ).digest()
            if not hmac.compare_digest(_decode_bytes(encoded_signature), expected_signature):
                raise ValueError
            payload = json.loads(_decode_bytes(encoded_payload))
            if not isinstance(payload, dict):
                raise ValueError
            capability = DiagnosisCapability(
                diagnosis_run_id=_required_str(payload, "run_id"),
                audience=_required_str(payload, "aud"),
                expires_at=datetime.fromtimestamp(_required_int(payload, "exp"), tz=UTC),
                nonce=_required_str(payload, "nonce"),
            )
            if _required_int(payload, "v") != CAPABILITY_VERSION:
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError, binascii.Error):
            raise DiagnosisCapabilityDenied("diagnosis_capability_invalid") from None

        if capability.audience != CAPABILITY_AUDIENCE:
            raise DiagnosisCapabilityDenied("diagnosis_capability_audience_denied")
        if capability.diagnosis_run_id != expected_run_id:
            raise DiagnosisCapabilityDenied("diagnosis_capability_run_denied")
        if _as_utc(self._clock()) >= capability.expires_at:
            raise DiagnosisCapabilityDenied("diagnosis_capability_expired")
        return capability


def _encode_json(value: dict[str, object]) -> str:
    return _encode_bytes(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    )


def _encode_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _decode_bytes(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _required_str(payload: dict[object, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError
    return value


def _required_int(payload: dict[object, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("diagnosis_capability_time_must_be_aware")
    return value.astimezone(UTC)
