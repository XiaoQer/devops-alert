from incident_intelligence.domain.alert_text_similarity import (
    deterministic_similarity,
    normalize_alert_text,
)


def test_normalization_separates_dynamic_identifiers_and_is_bounded() -> None:
    value = normalize_alert_text(
        "Pod api-7c9f9d8b5-x1 restarting",
        "request 9f7e3d6c-3a00-4e75-a286-8dcd2aaab111 failed at 10.0.0.8",
        "KubePodCrashLooping",
        "restart",
    )

    assert "9f7e3d6c" not in value.normalized
    assert "10.0.0.8" not in value.normalized
    assert "<uuid>" in value.normalized
    assert "<ip>" in value.normalized
    assert len(value.tokens) <= 256
    assert len(value.character_ngrams) <= 512


def test_deterministic_similarity_is_stable_for_dynamic_variants() -> None:
    first = normalize_alert_text(
        "支付 Pod api-7c9f9d8b5-x1 持续重启",
        "请求 9f7e3d6c-3a00-4e75-a286-8dcd2aaab111 失败",
        "KubePodCrashLooping",
        "restart",
    )
    second = normalize_alert_text(
        "支付 Pod api-6d8f8c7a4-z9 持续重启",
        "请求 1f7e3d6c-3a00-4e75-a286-8dcd2aaab222 失败",
        "KubePodCrashLooping",
        "restart",
    )

    score = deterministic_similarity(first, second)

    assert score == deterministic_similarity(first, second)
    assert 0.75 <= score <= 1.0


def test_unrelated_alert_text_has_low_similarity() -> None:
    database = normalize_alert_text("MySQL 行锁等待", "事务等待超过阈值", "LockWait", "lock")
    pod = normalize_alert_text("Pod 持续重启", "容器退出", "CrashLoop", "restart")

    assert deterministic_similarity(database, pod) < 0.5
