from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ServiceName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]

EntityType = Literal[
    "SERVICE",
    "WORKLOAD",
    "POD",
    "NODE",
    "JOB",
    "INSTANCE",
    "CLUSTER",
    "UNKNOWN",
]
ServiceResolutionStatus = Literal["RESOLVED", "PENDING", "UNRESOLVED", "NOT_APPLICABLE"]
ServiceResolutionSource = Literal["ALERT_LABEL", "KUBERNETES_POD_LABEL"]
ResolutionConfidence = Literal["HIGH", "MEDIUM"]
ResolutionReasonCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]


class EntityIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_type: EntityType
    entity_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    display_name: str = Field(min_length=1, max_length=257)
    service: ServiceName | None = None
    service_resolution_status: ServiceResolutionStatus
    service_resolution_source: ServiceResolutionSource | None = None
    service_resolution_confidence: ResolutionConfidence | None = None
    service_resolution_reason_codes: tuple[ResolutionReasonCode, ...] = Field(
        default=(), max_length=10
    )


def derive_entity_identity(labels: Mapping[str, str]) -> EntityIdentity:
    service = _value(labels, "service")
    if service is not None:
        return _identity(
            "SERVICE",
            (service,),
            service,
            service=service,
            status="RESOLVED",
            source="ALERT_LABEL",
            confidence="HIGH",
        )

    application = _value(labels, "app.kubernetes.io/name") or _value(labels, "app")
    if application is not None:
        return _identity(
            "SERVICE",
            (application,),
            application,
            service=application,
            status="RESOLVED",
            source="ALERT_LABEL",
            confidence="HIGH",
        )

    namespace = _value(labels, "namespace")
    workload = _value(labels, "workload")
    if workload is not None:
        parts = _qualified_parts(namespace, workload)
        return _identity("WORKLOAD", parts, "/".join(parts), status="PENDING")

    pod = _value(labels, "pod")
    if pod is not None:
        parts = _qualified_parts(namespace, pod)
        return _identity("POD", parts, "/".join(parts), status="PENDING")

    cluster = _value(labels, "cluster")
    node = _value(labels, "node")
    if node is not None:
        parts = _qualified_parts(cluster, node)
        return _identity(
            "NODE",
            parts,
            "/".join(parts),
            status="NOT_APPLICABLE",
            reason_codes=("service_not_applicable",),
        )

    job = _value(labels, "job")
    if job is not None:
        parts = _qualified_parts(namespace, job)
        return _identity("JOB", parts, "/".join(parts), status="PENDING")

    instance = _value(labels, "instance")
    if instance is not None:
        return _identity("INSTANCE", (instance,), instance, status="PENDING")

    if cluster is not None:
        return _identity(
            "CLUSTER",
            (cluster,),
            cluster,
            status="NOT_APPLICABLE",
            reason_codes=("service_not_applicable",),
        )

    return _identity(
        "UNKNOWN",
        ("unidentified",),
        "未识别对象",
        status="UNRESOLVED",
        reason_codes=("entity_hints_missing",),
    )


def _identity(
    entity_type: EntityType,
    parts: tuple[str, ...],
    display_name: str,
    *,
    service: str | None = None,
    status: ServiceResolutionStatus,
    source: ServiceResolutionSource | None = None,
    confidence: ResolutionConfidence | None = None,
    reason_codes: tuple[str, ...] | None = None,
) -> EntityIdentity:
    resolved_reasons = reason_codes
    if resolved_reasons is None:
        resolved_reasons = () if status == "RESOLVED" else ("service_missing",)
    canonical = "\0".join(("entity.v1", entity_type, *parts))
    return EntityIdentity(
        entity_type=entity_type,
        entity_key=sha256(canonical.encode()).hexdigest(),
        display_name=display_name,
        service=service,
        service_resolution_status=status,
        service_resolution_source=source,
        service_resolution_confidence=confidence,
        service_resolution_reason_codes=resolved_reasons,
    )


def _qualified_parts(qualifier: str | None, name: str) -> tuple[str, ...]:
    return (name,) if qualifier is None else (qualifier, name)


def _value(labels: Mapping[str, str], key: str) -> str | None:
    value = labels.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
