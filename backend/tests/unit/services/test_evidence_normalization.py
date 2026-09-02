from incident_intelligence.services.evidence_normalization import (
    normalize_log_message,
    redact_log_message,
)


def test_redacts_authorization_cookie_password_and_connection_string() -> None:
    text = (
        "Authorization: Bearer abc123 Cookie: sid=secret-value "
        "password=hunter2 mysql://user:dbpass@db/app"
    )

    sanitized = redact_log_message(text)

    assert "abc123" not in sanitized
    assert "secret-value" not in sanitized
    assert "hunter2" not in sanitized
    assert "user:dbpass" not in sanitized
    assert sanitized.count("[REDACTED]") >= 4


def test_normalize_log_message_collapses_whitespace_and_limits_length() -> None:
    normalized = normalize_log_message(" failure\n\t  detail " + "x" * 5_000)

    assert normalized.startswith("failure detail ")
    assert len(normalized) == 4_000
