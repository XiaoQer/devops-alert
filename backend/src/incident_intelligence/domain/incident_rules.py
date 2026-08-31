# ruff: noqa: RUF001

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from incident_intelligence.domain.models import Environment, Severity, UtcAwareDatetime

IncidentRuleState = Literal["DRAFT", "PUBLISHED", "DISABLED"]
RuleGroupBy = Literal["SERVICE", "ENTITY"]

RuleName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
RuleDescription = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=1_000),
]
RuleService = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
RuleSourceId = Annotated[str, StringConstraints(pattern=r"^src_[0-9a-f]{32}$")]


class _FrozenRuleModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DistinctAlertNamesCondition(_FrozenRuleModel):
    type: Literal["DISTINCT_ALERT_NAMES_GTE"]
    threshold: int = Field(ge=1, le=1_000)


class ActiveAlertsCondition(_FrozenRuleModel):
    type: Literal["ACTIVE_ALERTS_GTE"]
    threshold: int = Field(ge=1, le=1_000)


class DistinctEntitiesCondition(_FrozenRuleModel):
    type: Literal["DISTINCT_ENTITIES_GTE"]
    threshold: int = Field(ge=1, le=1_000)


class MaximumSeverityCondition(_FrozenRuleModel):
    type: Literal["MAX_SEVERITY_AT_LEAST"]
    severity: Severity


IncidentRuleCondition = Annotated[
    DistinctAlertNamesCondition
    | ActiveAlertsCondition
    | DistinctEntitiesCondition
    | MaximumSeverityCondition,
    Field(discriminator="type"),
]

COUNT_CONDITION_TYPES = {
    "DISTINCT_ALERT_NAMES_GTE",
    "ACTIVE_ALERTS_GTE",
    "DISTINCT_ENTITIES_GTE",
}


class IncidentRuleConfig(_FrozenRuleModel):
    environment: Environment
    alert_source_ids: tuple[RuleSourceId, ...] = Field(default=(), max_length=50)
    services: tuple[RuleService, ...] = Field(default=(), max_length=50)
    group_by: RuleGroupBy
    window_minutes: int = Field(ge=1, le=60)
    conditions: tuple[IncidentRuleCondition, ...] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def validate_conditions(self) -> IncidentRuleConfig:
        condition_types = [condition.type for condition in self.conditions]
        if len(condition_types) != len(set(condition_types)):
            raise ValueError("同一条件类型不能重复")
        if not COUNT_CONDITION_TYPES.intersection(condition_types):
            raise ValueError("至少需要一个计数条件")
        if len(self.alert_source_ids) != len(set(self.alert_source_ids)):
            raise ValueError("接入源不能重复")
        if len(self.services) != len(set(self.services)):
            raise ValueError("服务不能重复")
        return self


class IncidentRule(_FrozenRuleModel):
    id: str = Field(pattern=r"^irl_[0-9a-f]{32}$")
    name: RuleName
    description: RuleDescription
    state: IncidentRuleState
    config: IncidentRuleConfig
    summary: str = Field(min_length=1, max_length=2_000)
    version: int = Field(ge=1)
    last_successful_dry_run_id: str | None = Field(
        default=None,
        pattern=r"^ird_[0-9a-f]{32}$",
    )
    last_successful_dry_run_version: int | None = Field(default=None, ge=1)
    last_successful_dry_run_at: UtcAwareDatetime | None = None
    published_at: UtcAwareDatetime | None = None
    disabled_at: UtcAwareDatetime | None = None
    created_at: UtcAwareDatetime
    updated_at: UtcAwareDatetime

    @model_validator(mode="after")
    def validate_state_times(self) -> IncidentRule:
        dry_run_fields = (
            self.last_successful_dry_run_id,
            self.last_successful_dry_run_version,
            self.last_successful_dry_run_at,
        )
        if any(value is None for value in dry_run_fields) and any(
            value is not None for value in dry_run_fields
        ):
            raise ValueError("成功试运行字段必须同时存在或同时为空")
        if self.state == "DRAFT" and (
            self.published_at is not None or self.disabled_at is not None
        ):
            raise ValueError("草稿不能有发布或停用时间")
        if self.state == "PUBLISHED" and (
            self.published_at is None or self.disabled_at is not None
        ):
            raise ValueError("已发布规则的状态时间不完整")
        if self.state == "DISABLED" and (self.published_at is None or self.disabled_at is None):
            raise ValueError("已停用规则的状态时间不完整")
        return self


class RuleStateConflict(ValueError):
    pass


def create_rule(
    *,
    rule_id: str,
    name: str,
    description: str,
    config: IncidentRuleConfig,
    now: UtcAwareDatetime,
) -> IncidentRule:
    return IncidentRule(
        id=rule_id,
        name=name,
        description=description,
        state="DRAFT",
        config=config,
        summary=summarize_rule(config),
        version=1,
        created_at=now,
        updated_at=now,
    )


def update_draft(
    rule: IncidentRule,
    *,
    name: str,
    description: str,
    config: IncidentRuleConfig,
    now: UtcAwareDatetime,
) -> IncidentRule:
    _require_state(rule, "DRAFT", "draft_required")
    return _validated_copy(
        rule,
        name=name,
        description=description,
        config=config,
        summary=summarize_rule(config),
        version=rule.version + 1,
        last_successful_dry_run_id=None,
        last_successful_dry_run_version=None,
        last_successful_dry_run_at=None,
        updated_at=now,
    )


def record_successful_dry_run(
    rule: IncidentRule,
    *,
    dry_run_id: str,
    now: UtcAwareDatetime,
) -> IncidentRule:
    _require_state(rule, "DRAFT", "draft_required")
    return _validated_copy(
        rule,
        last_successful_dry_run_id=dry_run_id,
        last_successful_dry_run_version=rule.version,
        last_successful_dry_run_at=now,
        updated_at=now,
    )


def publish_rule(rule: IncidentRule, *, now: UtcAwareDatetime) -> IncidentRule:
    _require_state(rule, "DRAFT", "draft_required")
    if (
        rule.last_successful_dry_run_id is None
        or rule.last_successful_dry_run_version != rule.version
    ):
        raise RuleStateConflict("successful_dry_run_required")
    return _validated_copy(
        rule,
        state="PUBLISHED",
        version=rule.version + 1,
        published_at=now,
        updated_at=now,
    )


def disable_rule(rule: IncidentRule, *, now: UtcAwareDatetime) -> IncidentRule:
    _require_state(rule, "PUBLISHED", "published_required")
    return _validated_copy(
        rule,
        state="DISABLED",
        version=rule.version + 1,
        disabled_at=now,
        updated_at=now,
    )


def copy_rule(
    rule: IncidentRule,
    *,
    rule_id: str,
    name: str,
    now: UtcAwareDatetime,
) -> IncidentRule:
    return create_rule(
        rule_id=rule_id,
        name=name,
        description=rule.description,
        config=rule.config,
        now=now,
    )


def summarize_rule(config: IncidentRuleConfig) -> str:
    environment = {
        "production": "生产环境",
        "staging": "预发布环境",
        "development": "开发环境",
        "unknown": "未知环境",
    }.get(config.environment, f"{config.environment} 环境")
    filters: list[str] = []
    if config.alert_source_ids:
        filters.append(f"限定 {len(config.alert_source_ids)} 个接入源")
    if config.services:
        filters.append(f"服务 {'、'.join(config.services)}")
    scope = f"，{'、'.join(filters)}" if filters else ""
    group = "同一服务" if config.group_by == "SERVICE" else "同一实体"
    condition_summary = "，并且".join(_condition_summary(item) for item in config.conditions)
    return (
        f"{environment}内{scope}，按{group}分组，在 {config.window_minutes} 分钟内："
        f"{condition_summary}"
    )


def _condition_summary(condition: IncidentRuleCondition) -> str:
    if condition.type == "DISTINCT_ALERT_NAMES_GTE":
        return f"不同 Alertname 数量不少于 {condition.threshold}"
    if condition.type == "ACTIVE_ALERTS_GTE":
        return f"活动告警数量不少于 {condition.threshold}"
    if condition.type == "DISTINCT_ENTITIES_GTE":
        return f"不同实体数量不少于 {condition.threshold}"
    severity = {
        "critical": "严重",
        "high": "高",
        "medium": "中",
        "low": "低",
    }[condition.severity]
    return f"最高告警级别至少为{severity}"


def _require_state(
    rule: IncidentRule,
    expected: IncidentRuleState,
    reason: str,
) -> None:
    if rule.state != expected:
        raise RuleStateConflict(reason)


def _validated_copy(rule: IncidentRule, **updates: object) -> IncidentRule:
    return IncidentRule.model_validate({**rule.model_dump(), **updates})
