from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProblemScopeType = Literal["SERVICE", "WORKLOAD", "NAMESPACE", "CLUSTER", "JOB", "SOURCE"]

SIGNATURE_VERSION = "problem-signature.v1"
DEFAULT_WINDOW_SECONDS = 300
SOURCE_WINDOW_SECONDS = 120


class ProblemSignature(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    problem_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    problem_type: str = Field(min_length=1, max_length=200)
    scope_type: ProblemScopeType
    scope_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_display_name: str = Field(min_length=1, max_length=257)
    window_seconds: int = Field(ge=1, le=3_600)
    version: Literal["problem-signature.v1"] = "problem-signature.v1"


def derive_problem_signature(
    *,
    alert_source_id: str,
    problem_type: str,
    symptom: str,
    environment: str,
    facts: Mapping[str, str],
) -> ProblemSignature:
    normalized_problem_type = _bounded_text(problem_type, "未命名告警", 200)
    scope_type, scope_parts, scope_display_name = _derive_scope(alert_source_id, facts)
    scope_key = _digest("problem-scope.v1", scope_type, *scope_parts)
    problem_key = _digest(
        SIGNATURE_VERSION,
        alert_source_id,
        normalized_problem_type.casefold(),
        _bounded_text(symptom, "unknown", 64).casefold(),
        _bounded_text(environment, "unknown", 32).casefold(),
        scope_type,
        scope_key,
    )
    return ProblemSignature(
        problem_key=problem_key,
        problem_type=normalized_problem_type,
        scope_type=scope_type,
        scope_key=scope_key,
        scope_display_name=scope_display_name,
        window_seconds=(
            SOURCE_WINDOW_SECONDS if scope_type == "SOURCE" else DEFAULT_WINDOW_SECONDS
        ),
    )


def _derive_scope(
    alert_source_id: str,
    facts: Mapping[str, str],
) -> tuple[ProblemScopeType, tuple[str, ...], str]:
    service = _value(facts, "service")
    if service is not None:
        return "SERVICE", (service,), service

    cluster = _value(facts, "cluster")
    namespace = _value(facts, "namespace")
    workload = _value(facts, "workload")
    if workload is not None:
        parts = tuple(item for item in (cluster, namespace, workload) if item is not None)
        return "WORKLOAD", parts, "/".join(parts)

    if namespace is not None:
        parts = tuple(item for item in (cluster, namespace) if item is not None)
        return "NAMESPACE", parts, "/".join(parts)

    if cluster is not None:
        return "CLUSTER", (cluster,), cluster

    job = _value(facts, "job")
    if job is not None:
        return "JOB", (job,), job

    return "SOURCE", (alert_source_id,), alert_source_id


def _value(values: Mapping[str, str], key: str) -> str | None:
    value = values.get(key)
    normalized = " ".join(value.split()) if isinstance(value, str) else ""
    return normalized or None


def _bounded_text(value: str, fallback: str, limit: int) -> str:
    normalized = " ".join(value.split()) if isinstance(value, str) else ""
    return (normalized or fallback)[:limit]


def _digest(*parts: str) -> str:
    return sha256("\0".join(parts).encode("utf-8")).hexdigest()
