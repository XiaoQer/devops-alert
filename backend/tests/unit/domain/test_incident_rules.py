# ruff: noqa: RUF001

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from incident_intelligence.domain.incident_rules import (
    IncidentRuleConfig,
    RuleStateConflict,
    copy_rule,
    create_rule,
    disable_rule,
    publish_rule,
    record_successful_dry_run,
    summarize_rule,
    update_draft,
)

NOW = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)
RULE_ID = "irl_11111111111111111111111111111111"
DRY_RUN_ID = "ird_22222222222222222222222222222222"


def _config(**overrides: object) -> IncidentRuleConfig:
    values: dict[str, object] = {
        "environment": "production",
        "alert_source_ids": (),
        "services": (),
        "group_by": "SERVICE",
        "window_minutes": 5,
        "conditions": (
            {"type": "DISTINCT_ALERT_NAMES_GTE", "threshold": 2},
            {"type": "MAX_SEVERITY_AT_LEAST", "severity": "high"},
        ),
    }
    values.update(overrides)
    return IncidentRuleConfig.model_validate(values)


def _draft():
    return create_rule(
        rule_id=RULE_ID,
        name="支付链路异常",
        description="识别支付服务短时间内的多类告警",
        config=_config(),
        now=NOW,
    )


def _tested_draft():
    return record_successful_dry_run(_draft(), dry_run_id=DRY_RUN_ID, now=NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"environment": ""},
        {"window_minutes": 0},
        {"window_minutes": 61},
        {"conditions": ({"type": "MAX_SEVERITY_AT_LEAST", "severity": "high"},)},
        {
            "conditions": (
                {"type": "ACTIVE_ALERTS_GTE", "threshold": 2},
                {"type": "ACTIVE_ALERTS_GTE", "threshold": 3},
            )
        },
    ],
)
def test_rule_config_rejects_invalid_boundaries(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _config(**changes)


def test_rule_config_rejects_unknown_fields_and_invalid_source_ids() -> None:
    with pytest.raises(ValidationError):
        _config(alert_source_ids=("not-a-source",))
    with pytest.raises(ValidationError):
        IncidentRuleConfig.model_validate({**_config().model_dump(), "expression": "alert > 2"})


def test_create_rule_builds_backend_owned_chinese_summary() -> None:
    rule = _draft()

    assert rule.state == "DRAFT"
    assert rule.version == 1
    assert rule.summary == (
        "生产环境内，按同一服务分组，在 5 分钟内："
        "不同 Alertname 数量不少于 2，并且最高告警级别至少为高"
    )
    assert rule.last_successful_dry_run_version is None


def test_summary_includes_optional_sources_and_services_without_raw_json() -> None:
    summary = summarize_rule(
        _config(
            alert_source_ids=("src_" + "3" * 32,),
            services=("checkout", "payment"),
            group_by="ENTITY",
            conditions=(
                {"type": "ACTIVE_ALERTS_GTE", "threshold": 3},
                {"type": "DISTINCT_ENTITIES_GTE", "threshold": 2},
            ),
        )
    )

    assert summary == (
        "生产环境内，限定 1 个接入源、服务 checkout、payment，按同一实体分组，"
        "在 5 分钟内：活动告警数量不少于 3，并且不同实体数量不少于 2"
    )
    assert "{" not in summary


def test_modifying_draft_increments_version_and_invalidates_dry_run() -> None:
    tested = _tested_draft()

    changed = update_draft(
        tested,
        name="支付链路持续异常",
        description=tested.description,
        config=tested.config,
        now=NOW + timedelta(minutes=1),
    )

    assert changed.version == tested.version + 1
    assert changed.name == "支付链路持续异常"
    assert changed.last_successful_dry_run_id is None
    assert changed.last_successful_dry_run_version is None
    assert changed.last_successful_dry_run_at is None


def test_publishing_requires_current_successful_dry_run() -> None:
    with pytest.raises(RuleStateConflict, match="successful_dry_run_required"):
        publish_rule(_draft(), now=NOW)

    published = publish_rule(_tested_draft(), now=NOW + timedelta(minutes=1))

    assert published.state == "PUBLISHED"
    assert published.published_at == NOW + timedelta(minutes=1)
    assert published.version == 2


def test_published_rule_is_immutable_and_can_be_disabled() -> None:
    published = publish_rule(_tested_draft(), now=NOW + timedelta(minutes=1))

    with pytest.raises(RuleStateConflict, match="draft_required"):
        update_draft(
            published,
            name="不能覆盖",
            description=published.description,
            config=published.config,
            now=NOW + timedelta(minutes=2),
        )

    disabled = disable_rule(published, now=NOW + timedelta(minutes=2))
    assert disabled.state == "DISABLED"
    assert disabled.disabled_at == NOW + timedelta(minutes=2)
    assert disabled.version == published.version + 1


def test_copying_published_rule_creates_independent_clean_draft() -> None:
    published = publish_rule(_tested_draft(), now=NOW + timedelta(minutes=1))

    copied = copy_rule(
        published,
        rule_id="irl_44444444444444444444444444444444",
        name="支付链路异常（副本）",
        now=NOW + timedelta(minutes=2),
    )

    assert copied.id != published.id
    assert copied.state == "DRAFT"
    assert copied.version == 1
    assert copied.config == published.config
    assert copied.last_successful_dry_run_id is None
    assert copied.published_at is None
    assert copied.disabled_at is None


def test_only_published_rule_can_be_disabled() -> None:
    with pytest.raises(RuleStateConflict, match="published_required"):
        disable_rule(_draft(), now=NOW)
