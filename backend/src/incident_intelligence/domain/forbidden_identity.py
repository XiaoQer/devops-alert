from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

FORBIDDEN_NORMALIZED_KEYS = frozenset(
    {
        "scenarioid",
        "scenarioversion",
        "experimentid",
        "injectionaction",
        "groundtruth",
        "注入动作",
        "标准答案",
    }
)


@dataclass(frozen=True, slots=True)
class ForbiddenIdentityError(Exception):
    key: str
    reason_code: str = "forbidden_identity"

    def __str__(self) -> str:
        return f"forbidden identity field: {self.key}"


def _normalized_key(key: str) -> str:
    return "".join(character for character in key.casefold() if character.isalnum())


def reject_forbidden_identity(value: object) -> None:
    _walk(value, seen=set())


def _walk(value: object, *, seen: set[int]) -> None:
    if isinstance(value, (str, bytes, bytearray)):
        return

    identity = id(value)
    if identity in seen:
        return

    if isinstance(value, Mapping):
        seen.add(identity)
        for key, nested_value in value.items():
            if isinstance(key, str) and _normalized_key(key) in FORBIDDEN_NORMALIZED_KEYS:
                raise ForbiddenIdentityError(key=key)
            _walk(nested_value, seen=seen)
        return

    if isinstance(value, Sequence):
        seen.add(identity)
        for nested_value in value:
            _walk(nested_value, seen=seen)
