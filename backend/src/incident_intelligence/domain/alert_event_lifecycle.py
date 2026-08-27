from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AlertEventState = Literal["FORMING", "ACTIVE", "OBSERVING", "CLOSED"]


class LifecyclePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    forming_seconds: int = Field(default=30, ge=1, le=300)
    observing_seconds: int = Field(default=300, ge=30, le=3600)
    allowed_lateness_seconds: int = Field(default=300, ge=0, le=3600)
    member_limit: int = Field(default=1_000, ge=1, le=1_000)


class LifecycleContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: AlertEventState
    active_count: int = Field(ge=0)
    now: datetime
    forming_until: datetime | None = None
    observing_until: datetime | None = None
    closed_at: datetime | None = None
    allow_late_correction: bool = False


class LifecycleDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: AlertEventState
    schedule_at: datetime | None
    forming_until: datetime | None
    observing_until: datetime | None
    closed_at: datetime | None
    reason_code: str = Field(min_length=1, max_length=64)
    explanation: str = Field(min_length=1, max_length=300)


def decide_lifecycle(
    context: LifecycleContext,
    policy: LifecyclePolicy | None = None,
) -> LifecycleDecision:
    selected_policy = policy or LifecyclePolicy()

    if context.state == "CLOSED":
        return _decide_closed(context, selected_policy)

    if context.state == "FORMING" and _is_future(context.forming_until, context.now):
        return _decision(
            context,
            state="FORMING",
            schedule_at=context.forming_until,
            forming_until=context.forming_until,
            reason_code="forming_window_active",
            explanation="告警事件已经可见, 仍在收集首批相关告警。",
        )

    if context.active_count > 0:
        if context.state == "OBSERVING":
            reason_code = "activity_resumed_during_observation"
            explanation = "恢复观察期内再次出现活动告警, 事件已恢复为活跃。"
        elif context.state == "FORMING":
            reason_code = "forming_window_completed"
            explanation = "首批聚合窗口结束, 事件仍有活动告警。"
        else:
            reason_code = "active_members_present"
            explanation = "事件仍有活动告警, 保持活跃。"
        return _decision(
            context,
            state="ACTIVE",
            schedule_at=None,
            observing_until=None,
            closed_at=None,
            reason_code=reason_code,
            explanation=explanation,
        )

    if context.state == "OBSERVING" and _is_future(context.observing_until, context.now):
        return _decision(
            context,
            state="OBSERVING",
            schedule_at=context.observing_until,
            reason_code="recovery_observation_active",
            explanation="所有成员均已恢复, 正在等待恢复观察期结束。",
        )

    if context.state == "OBSERVING" and context.observing_until is not None:
        return _decision(
            context,
            state="CLOSED",
            schedule_at=None,
            observing_until=None,
            closed_at=context.now,
            reason_code="recovery_observation_completed",
            explanation="恢复观察期内没有再次触发, 事件已关闭。",
        )

    observing_until = context.now + timedelta(seconds=selected_policy.observing_seconds)
    return _decision(
        context,
        state="OBSERVING",
        schedule_at=observing_until,
        forming_until=None,
        observing_until=observing_until,
        closed_at=None,
        reason_code="all_members_resolved",
        explanation="所有成员均已恢复, 事件进入恢复观察期。",
    )


def _decide_closed(
    context: LifecycleContext,
    policy: LifecyclePolicy,
) -> LifecycleDecision:
    can_correct = (
        context.allow_late_correction
        and context.active_count > 0
        and context.closed_at is not None
        and context.now <= context.closed_at + timedelta(seconds=policy.allowed_lateness_seconds)
    )
    if can_correct:
        return _decision(
            context,
            state="ACTIVE",
            schedule_at=None,
            observing_until=None,
            closed_at=None,
            reason_code="late_member_corrected_closed_state",
            explanation="容忍窗口内到达的迟到告警证明事件仍在活动, 已纠正关闭状态。",
        )
    if context.allow_late_correction and context.active_count > 0:
        reason_code = "late_correction_window_expired"
        explanation = "迟到告警已超过状态纠正窗口, 原事件保持关闭。"
    else:
        reason_code = "closed_event_is_terminal"
        explanation = "已关闭事件不会因普通新触发重新打开。"
    return _decision(
        context,
        state="CLOSED",
        schedule_at=None,
        reason_code=reason_code,
        explanation=explanation,
    )


def _is_future(value: datetime | None, now: datetime) -> bool:
    return value is not None and value > now


def _decision(
    context: LifecycleContext,
    *,
    state: AlertEventState,
    schedule_at: datetime | None,
    reason_code: str,
    explanation: str,
    forming_until: datetime | None = None,
    observing_until: datetime | None = None,
    closed_at: datetime | None = None,
) -> LifecycleDecision:
    return LifecycleDecision(
        state=state,
        schedule_at=schedule_at,
        forming_until=forming_until,
        observing_until=observing_until,
        closed_at=closed_at if state != "CLOSED" else closed_at or context.closed_at,
        reason_code=reason_code,
        explanation=explanation,
    )
