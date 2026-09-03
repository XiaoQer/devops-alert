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
        "common-service:v2",
        "http:v2",
        "mysql:v2",
    )


def test_registry_matches_jvm_case_insensitively() -> None:
    selected = EvidencePackRegistry.default().resolve(
        ("jvmhighgcpause",),
        {},
    )

    assert tuple(pack.id for pack in selected) == ("common-service", "jvm")


def test_mysql_pack_collects_real_exporter_row_lock_metrics() -> None:
    selected = EvidencePackRegistry.default().resolve(
        ("MySQLRowLockWaitActive",),
        {},
    )

    mysql_pack = next(pack for pack in selected if pack.id == "mysql")
    prometheus_queries = {
        template.query_name: template.controlled_query
        for template in mysql_pack.templates
        if template.source_type == "PROMETHEUS"
    }

    assert prometheus_queries == {
        "prom.mysql.pool": (
            "db_client_connections_usage{${environment_matcher}${service_matcher}}"
        ),
        "prom.mysql.row-lock-current-waits": (
            "mysql_global_status_innodb_row_lock_current_waits"
            "{${environment_matcher}${service_matcher}}"
        ),
        "prom.mysql.row-lock-waits-increase": (
            "increase(mysql_global_status_innodb_row_lock_waits"
            "{${environment_matcher}${service_matcher}}[5m])"
        ),
        "prom.mysql.row-lock-time-increase": (
            "increase(mysql_global_status_innodb_row_lock_time"
            "{${environment_matcher}${service_matcher}}[5m])"
        ),
    }
    assert mysql_pack.version == 2
    assert all(
        template.version == 2
        for template in mysql_pack.templates
        if template.source_type == "PROMETHEUS"
    )


def test_registry_rejects_duplicate_template_identity() -> None:
    registry = EvidencePackRegistry.default()
    duplicate = registry.packs[0].model_copy(update={"id": "duplicate", "priority": 99})

    with pytest.raises(ValueError, match="duplicate_evidence_template"):
        EvidencePackRegistry((*registry.packs, duplicate))
