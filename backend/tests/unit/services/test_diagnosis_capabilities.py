from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from incident_intelligence.services.diagnosis_capabilities import (
    DiagnosisCapabilityDenied,
    DiagnosisCapabilityIssuer,
)

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
RUN_ID = f"drun_{'1' * 32}"
OTHER_RUN_ID = f"drun_{'2' * 32}"


def test_capability_cannot_read_another_diagnosis_run() -> None:
    issuer = DiagnosisCapabilityIssuer(
        hmac_secret="test-only-hmac-secret",
        clock=lambda: NOW,
        nonce_factory=lambda: "nonce-for-test",
    )
    token = issuer.issue(RUN_ID, expires_at=NOW + timedelta(minutes=5))

    with pytest.raises(DiagnosisCapabilityDenied, match="diagnosis_capability_run_denied"):
        issuer.verify(token, expected_run_id=OTHER_RUN_ID)


def test_capability_rejects_expired_or_tampered_token() -> None:
    issuer = DiagnosisCapabilityIssuer(
        hmac_secret="test-only-hmac-secret",
        clock=lambda: NOW,
        nonce_factory=lambda: "nonce-for-test",
    )
    expired = issuer.issue(RUN_ID, expires_at=NOW + timedelta(seconds=1))
    valid = issuer.issue(RUN_ID, expires_at=NOW + timedelta(minutes=5))

    later = DiagnosisCapabilityIssuer(
        hmac_secret="test-only-hmac-secret",
        clock=lambda: NOW + timedelta(seconds=1),
    )
    with pytest.raises(DiagnosisCapabilityDenied, match="diagnosis_capability_expired"):
        later.verify(expired, expected_run_id=RUN_ID)
    with pytest.raises(DiagnosisCapabilityDenied, match="diagnosis_capability_invalid"):
        issuer.verify(f"{valid}x", expected_run_id=RUN_ID)
