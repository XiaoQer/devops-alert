from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True, slots=True)
class AdapterValidationError(Exception):
    reason_code: str

    def __str__(self) -> str:
        return self.reason_code


def normalize_source_uri(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise AdapterValidationError("invalid_source_uri") from error
    if (
        not parsed.scheme
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise AdapterValidationError("invalid_source_uri")
    hostname = parsed.hostname.casefold()
    host = f"[{hostname}]" if ":" in hostname else hostname
    authority = f"{host}:{port}" if port is not None else host
    return urlunsplit((parsed.scheme.casefold(), authority, parsed.path, "", ""))
