from __future__ import annotations

import pytest

from incident_intelligence.domain.evidence_packs import EvidencePackRegistry


def test_registry_always_selects_common_and_matches_specialized_packs() -> None:
    registry = EvidencePackRegistry.default()

    selected = registry.resolve(
        ("HighHttpErrorRate", "MySQLRowLockWaitActive"),
        {"component": "mysql"},
    )

    assert tuple(f"{pack.id}:v{pack.version}" for pack in selected) == (
        "common-service:v1",
        "http:v1",
        "mysql:v1",
    )


def test_registry_matches_jvm_case_insensitively() -> None:
    selected = EvidencePackRegistry.default().resolve(
        ("jvmhighgcpause",),
        {},
    )

    assert tuple(pack.id for pack in selected) == ("common-service", "jvm")


def test_registry_rejects_duplicate_template_identity() -> None:
    registry = EvidencePackRegistry.default()
    duplicate = registry.packs[0].model_copy(update={"id": "duplicate", "priority": 99})

    with pytest.raises(ValueError, match="duplicate_evidence_template"):
        EvidencePackRegistry((*registry.packs, duplicate))
