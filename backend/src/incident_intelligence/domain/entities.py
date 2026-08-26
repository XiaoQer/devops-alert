from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ServiceName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
EntityType = Literal["SERVICE", "WORKLOAD", "POD", "NODE", "JOB", "INSTANCE", "CLUSTER", "UNKNOWN"]


class EntityIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_type: EntityType
    entity_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    display_name: str = Field(min_length=1, max_length=257)
    service: ServiceName | None = None


def derive_entity_identity(labels: Mapping[str, str]) -> EntityIdentity:
    service = _value(labels, "service")
    if service is not None:
        return _identity("SERVICE", (service,), service, service)

    namespace = _value(labels, "namespace")
    workload = _value(labels, "workload")
    if workload is not None:
        return _qualified_identity("WORKLOAD", namespace, workload)

    pod = _value(labels, "pod")
    if pod is not None:
        return _qualified_identity("POD", namespace, pod)

    cluster = _value(labels, "cluster")
    node = _value(labels, "node")
    if node is not None:
        return _qualified_identity("NODE", cluster, node)

    job = _value(labels, "job")
    if job is not None:
        return _qualified_identity("JOB", namespace, job)

    instance = _value(labels, "instance")
    if instance is not None:
        return _identity("INSTANCE", (instance,), instance)

    if cluster is not None:
        return _identity("CLUSTER", (cluster,), cluster)

    return _identity("UNKNOWN", ("unidentified",), "对象未提供")


def _qualified_identity(
    entity_type: EntityType, qualifier: str | None, name: str
) -> EntityIdentity:
    parts = (name,) if qualifier is None else (qualifier, name)
    return _identity(entity_type, parts, "/".join(parts))


def _identity(
    entity_type: EntityType,
    parts: tuple[str, ...],
    display_name: str,
    service: str | None = None,
) -> EntityIdentity:
    canonical = "\0".join(("entity.v1", entity_type, *parts))
    return EntityIdentity(
        entity_type=entity_type,
        entity_key=sha256(canonical.encode()).hexdigest(),
        display_name=display_name,
        service=service,
    )


def _value(labels: Mapping[str, str], key: str) -> str | None:
    value = labels.get(key)
    normalized = value.strip() if isinstance(value, str) else ""
    return normalized or None
