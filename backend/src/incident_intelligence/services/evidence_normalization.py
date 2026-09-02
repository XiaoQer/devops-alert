from __future__ import annotations

import re
from hashlib import sha256

_REDACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+[^\s,;]+"),
    re.compile(r"(?i)\bcookie\s*:\s*[^\s,;]+"),
    re.compile(r"(?i)\b(?:password|passwd|pwd)\s*[=:]\s*[^\s,;]+"),
    re.compile(r"(?i)\b(?:mysql|postgres(?:ql)?|mongodb|redis)://[^\s/@:]+:[^\s/@]+@"),
)


def redact_log_message(text: str) -> str:
    sanitized = text
    for pattern in _REDACTION_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized


def normalize_log_message(text: str) -> str:
    collapsed = " ".join(text.split())
    return redact_log_message(collapsed)[:4_000]


def log_fingerprint(error_type: str, sanitized_message: str) -> str:
    canonical = f"{error_type.strip().casefold()}\x1f{sanitized_message.casefold()}"
    return sha256(canonical.encode()).hexdigest()
