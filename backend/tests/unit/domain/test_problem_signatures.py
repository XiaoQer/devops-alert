from incident_intelligence.domain.problem_signatures import derive_problem_signature

SOURCE_ID = "src_" + "a" * 32


def signature(**overrides: object):
    values: dict[str, object] = {
        "alert_source_id": SOURCE_ID,
        "problem_type": "KubePodNotReady",
        "symptom": "unknown",
        "environment": "unknown",
        "facts": {"cluster": "docker-desktop", "namespace": "payments"},
    }
    values.update(overrides)
    return derive_problem_signature(**values)


def test_scope_priority_is_deterministic() -> None:
    cases = (
        ({"service": "payment-api", "namespace": "payments"}, "SERVICE", "payment-api"),
        (
            {
                "cluster": "cluster-a",
                "namespace": "payments",
                "workload": "payment-worker",
            },
            "WORKLOAD",
            "cluster-a/payments/payment-worker",
        ),
        (
            {"cluster": "cluster-a", "namespace": "payments", "pod": "payment-0"},
            "NAMESPACE",
            "cluster-a/payments",
        ),
        ({"cluster": "cluster-a", "node": "node-1"}, "CLUSTER", "cluster-a"),
        ({"job": "mysql-backup"}, "JOB", "mysql-backup"),
        ({"pod": "orphan-pod"}, "SOURCE", SOURCE_ID),
    )

    for facts, expected_type, expected_name in cases:
        result = signature(facts=facts)
        assert result.scope_type == expected_type
        assert result.scope_display_name == expected_name
        assert len(result.scope_key) == 64
        assert len(result.problem_key) == 64


def test_resource_instances_do_not_split_the_same_namespace_problem() -> None:
    first = signature(
        facts={
            "cluster": "cluster-a",
            "namespace": "payments",
            "pod": "payment-0",
            "node": "node-1",
            "instance": "10.0.0.1:8080",
            "container": "api",
        }
    )
    second = signature(
        facts={
            "cluster": "cluster-a",
            "namespace": "payments",
            "pod": "payment-1",
            "node": "node-2",
            "instance": "10.0.0.2:8080",
            "container": "sidecar",
        }
    )

    assert first.problem_key == second.problem_key
    assert first.scope_key == second.scope_key


def test_problem_boundaries_change_the_problem_key() -> None:
    baseline = signature().problem_key
    variants = (
        signature(alert_source_id="src_" + "b" * 32).problem_key,
        signature(problem_type="KubeDeploymentReplicasMismatch").problem_key,
        signature(symptom="availability").problem_key,
        signature(environment="production").problem_key,
        signature(facts={"cluster": "docker-desktop", "namespace": "orders"}).problem_key,
    )

    assert all(item != baseline for item in variants)


def test_source_scope_uses_shorter_window_and_normalizes_problem_type() -> None:
    source_scoped = signature(problem_type="  KubePodNotReady  ", facts={"pod": "demo-0"})
    namespaced = signature(problem_type="KubePodNotReady")

    assert source_scoped.problem_type == "KubePodNotReady"
    assert source_scoped.scope_type == "SOURCE"
    assert source_scoped.window_seconds == 120
    assert namespaced.window_seconds == 300
    assert source_scoped.version == "problem-signature.v1"
