from incident_intelligence.domain.entities import derive_entity_identity


def test_pod_identity_is_pending_without_service() -> None:
    identity = derive_entity_identity({"namespace": "devops-platform", "pod": "demo-0"})

    assert identity.entity_type == "POD"
    assert identity.display_name == "devops-platform/demo-0"
    assert identity.service is None
    assert identity.service_resolution_status == "PENDING"
    assert identity.service_resolution_source is None
    assert identity.service_resolution_confidence is None
    assert identity.service_resolution_reason_codes == ("service_missing",)
    assert len(identity.entity_key) == 64


def test_explicit_service_has_priority_over_resource_labels() -> None:
    identity = derive_entity_identity(
        {
            "service": "payment-api",
            "namespace": "production",
            "pod": "payment-api-0",
        }
    )

    assert identity.entity_type == "SERVICE"
    assert identity.display_name == "payment-api"
    assert identity.service == "payment-api"
    assert identity.service_resolution_status == "RESOLVED"
    assert identity.service_resolution_source == "ALERT_LABEL"
    assert identity.service_resolution_confidence == "HIGH"
    assert identity.service_resolution_reason_codes == ()


def test_node_identity_does_not_invent_a_business_service() -> None:
    identity = derive_entity_identity({"cluster": "docker-desktop", "node": "worker-1"})

    assert identity.entity_type == "NODE"
    assert identity.display_name == "docker-desktop/worker-1"
    assert identity.service is None
    assert identity.service_resolution_status == "NOT_APPLICABLE"
    assert identity.service_resolution_reason_codes == ("service_not_applicable",)


def test_entity_key_is_stable_and_separates_namespaces() -> None:
    first = derive_entity_identity({"namespace": "a", "pod": "demo-0"})
    replay = derive_entity_identity({"namespace": "a", "pod": "demo-0"})
    other_namespace = derive_entity_identity({"namespace": "b", "pod": "demo-0"})

    assert first.entity_key == replay.entity_key
    assert first.entity_key != other_namespace.entity_key
