# ruff: noqa: RUF001

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from incident_intelligence.adapters.monitoring_http import (
    AdapterEvidenceResult,
    MonitoringPermanentError,
    MonitoringRetryableError,
)
from incident_intelligence.domain.evidence import (
    EvidenceItem,
    EvidenceItemState,
    EvidenceRun,
    summarize_run_status,
)
from incident_intelligence.domain.evidence_packs import EvidencePackRegistry
from incident_intelligence.domain.incidents import IncidentActivity, IncidentActivityKind
from incident_intelligence.ids import IdPrefix, new_id
from incident_intelligence.persistence.evidence_repository import (
    EvidenceRunRepository,
    EvidenceTaskRecord,
    EvidenceTaskRepository,
    MonitoringDataSourceRecord,
    MonitoringDataSourceRepository,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.evidence_correlation import EvidenceCorrelationService
from incident_intelligence.services.evidence_planning import (
    EvidenceExecutionPlan,
    EvidencePlanningService,
    EvidenceQueryRequest,
)


class EvidenceAdapter(Protocol):
    def collect(self, request: EvidenceQueryRequest) -> AdapterEvidenceResult: ...


class EvidenceAdapterFactory(Protocol):
    def create(self, source: MonitoringDataSourceRecord) -> EvidenceAdapter: ...


class EvidenceCollectionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    evidence_run_id: str
    outcome: str
    succeeded_count: int = 0
    skipped_count: int = 0
    missing_count: int = 0
    failed_count: int = 0
    replayed: bool = False


class EvidenceCollectionService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        adapter_factory: EvidenceAdapterFactory,
        planning: EvidencePlanningService | None = None,
        correlation: EvidenceCorrelationService | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[IdPrefix], str] = new_id,
        owner: str = "evidence-collection",
        lease_seconds: int = 120,
    ) -> None:
        self._uow_factory = uow_factory
        self._adapter_factory = adapter_factory
        self._planning = planning or EvidencePlanningService(EvidencePackRegistry.default())
        self._correlation = correlation or EvidenceCorrelationService()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory
        self._owner = owner
        self._lease_seconds = lease_seconds

    def process(self, task_id: str) -> EvidenceCollectionResult:
        now = self._clock().astimezone(UTC)
        started = self._start(task_id, now=now)
        if isinstance(started, EvidenceCollectionResult):
            return started
        task, run, plan, sources, existing_keys = started

        for request in plan.items:
            if request.execution_key in existing_keys:
                continue
            result = self._collect_one(request, sources.get(request.source_type))
            if isinstance(result, MonitoringRetryableError):
                if task.attempt_count < 5:
                    self._reschedule(task.id, error_code=_error_code(result), now=now)
                    return EvidenceCollectionResult(
                        task_id=task.id,
                        evidence_run_id=run.id,
                        outcome="RETRY_SCHEDULED",
                    )
                result = AdapterEvidenceResult(
                    state="FAILED",
                    interpretation="监控数据源多次暂时不可用，本次取证未取得该项数据。",
                    error_code=_error_code(result),
                )
            self._append_item(_item_from_result(run, request, result, self._id_factory, now))

        return self._finalize(task.id, run.id, now=now)

    def _start(
        self,
        task_id: str,
        *,
        now: datetime,
    ) -> (
        tuple[
            EvidenceTaskRecord,
            EvidenceRun,
            EvidenceExecutionPlan,
            dict[str, MonitoringDataSourceRecord],
            set[str],
        ]
        | EvidenceCollectionResult
    ):
        with self._uow_factory() as uow:
            tasks = _tasks(uow)
            task = tasks.get(task_id)
            if task is None:
                raise RuntimeError("evidence_collection_task_not_found")
            run = _runs(uow).get(task.evidence_run_id, for_update=True)
            if run is None:
                raise RuntimeError("evidence_run_not_found")
            if task.state == "SUCCEEDED" or run.state in {"SUCCEEDED", "PARTIAL", "FAILED"}:
                return _completed_result(task, run)
            if task.state == "PENDING":
                claimed = tasks.claim_due(
                    task.id,
                    owner=self._owner,
                    now=now,
                    lease_until=now + timedelta(seconds=self._lease_seconds),
                )
                if not claimed:
                    raise RuntimeError("evidence_collection_task_not_claimable")
                task = tasks.get(task.id)
                if task is None:
                    raise RuntimeError("evidence_collection_task_not_found")
            if task.state != "LEASED" or task.lease_owner != self._owner:
                raise RuntimeError("evidence_collection_task_not_owned")

            plan = self._planning.plan(run.context, run.window)
            if run.state == "QUEUED":
                package_versions = {
                    pack_id: int(version.removeprefix("v"))
                    for pack_id, version in (
                        item.rsplit(":", maxsplit=1) for item in plan.pack_versions
                    )
                }
                running = run.model_copy(
                    update={
                        "state": "RUNNING",
                        "package_versions": package_versions,
                        "started_at": now,
                        "version": run.version + 1,
                    }
                )
                if not _runs(uow).update(running, expected_version=run.version):
                    raise RuntimeError("evidence_run_concurrent_update")
                run = running
            source_repository = _sources(uow)
            sources = {
                source_type: source
                for source_type in ("PROMETHEUS", "ELASTICSEARCH", "SKYWALKING")
                if (
                    source := source_repository.find_enabled(
                        environment=run.context.environment,
                        source_type=source_type,
                    )
                )
                is not None
            }
            existing_keys = {item.evidence_key for item in _runs(uow).list_items(run.id)}
            uow.commit()
            return task, run, plan, sources, existing_keys

    def _collect_one(
        self,
        request: EvidenceQueryRequest,
        source: MonitoringDataSourceRecord | None,
    ) -> AdapterEvidenceResult | MonitoringRetryableError:
        if request.pre_result_state == "MISSING_TARGET":
            return AdapterEvidenceResult(
                state="MISSING_TARGET",
                interpretation="缺少服务标识，未执行该项监控查询。",
                error_code="monitoring_target_missing",
            )
        if source is None:
            return AdapterEvidenceResult(
                state="SKIPPED_DEPENDENCY",
                interpretation="当前环境未配置该类监控数据源，已跳过该项取证。",
                error_code="monitoring_source_not_configured",
            )
        try:
            return self._adapter_factory.create(source).collect(request)
        except MonitoringRetryableError as error:
            return error
        except MonitoringPermanentError as error:
            return AdapterEvidenceResult(
                state="FAILED",
                interpretation="监控查询未成功，已保留错误代码供排查。",
                error_code=_error_code(error),
            )

    def _append_item(self, item: EvidenceItem) -> None:
        with self._uow_factory() as uow:
            _runs(uow).append_item(item)
            uow.commit()

    def _reschedule(self, task_id: str, *, error_code: str, now: datetime) -> None:
        with self._uow_factory() as uow:
            if not _tasks(uow).reschedule(
                task_id,
                owner=self._owner,
                error_code=error_code,
                next_attempt_at=now + timedelta(seconds=30),
                now=now,
            ):
                raise RuntimeError("evidence_collection_task_lease_lost")
            uow.commit()

    def _finalize(
        self,
        task_id: str,
        run_id: str,
        *,
        now: datetime,
    ) -> EvidenceCollectionResult:
        with self._uow_factory() as uow:
            runs = _runs(uow)
            run = runs.get(run_id, for_update=True)
            if run is None:
                raise RuntimeError("evidence_run_not_found")
            items = runs.list_items(run.id, limit=41)
            if len(items) > 40:
                raise RuntimeError("evidence_item_limit_exceeded")
            existing_keys = {item.evidence_key for item in items}
            for draft in self._correlation.correlate(run.context, items):
                if draft.evidence_key in existing_keys:
                    continue
                runs.append_item(
                    EvidenceItem(
                        id=self._id_factory("evitem"),
                        evidence_run_id=run.id,
                        evidence_key=draft.evidence_key,
                        display_name=draft.display_name,
                        source_type=draft.source_type,
                        state=draft.state,
                        package_id="platform-correlation",
                        package_version=1,
                        template_id="cross-source.trace-log-match",
                        template_version=1,
                        evidence_type=draft.evidence_type,
                        query_started_at=draft.query_started_at,
                        query_ended_at=draft.query_ended_at,
                        interpretation=draft.interpretation,
                        normalized_result=draft.normalized_result,
                        error_code=draft.error_code,
                        created_at=now,
                    )
                )
            items = runs.list_items(run.id, limit=41)
            states = tuple(item.state for item in items)
            outcome = summarize_run_status(states)
            succeeded, skipped, missing, failed = _counts(states)
            completed = run.model_copy(
                update={
                    "state": outcome,
                    "succeeded_count": succeeded,
                    "skipped_count": skipped,
                    "missing_count": missing,
                    "failed_count": failed,
                    "failure_summary": (None if failed == 0 else f"{failed} 项监控查询未成功"),
                    "completed_at": now,
                    "version": run.version + 1,
                }
            )
            if not runs.update(completed, expected_version=run.version):
                raise RuntimeError("evidence_run_concurrent_update")
            _append_completion_activity(uow, completed, id_factory=self._id_factory, now=now)
            if not _tasks(uow).complete(task_id, owner=self._owner, now=now):
                raise RuntimeError("evidence_collection_task_lease_lost")
            uow.commit()
            return _completed_result(_tasks(uow).get(task_id), completed)


def _item_from_result(
    run: EvidenceRun,
    request: EvidenceQueryRequest,
    result: AdapterEvidenceResult,
    id_factory: Callable[[IdPrefix], str],
    now: datetime,
) -> EvidenceItem:
    return EvidenceItem(
        id=id_factory("evitem"),
        evidence_run_id=run.id,
        evidence_key=request.execution_key,
        display_name=request.display_name,
        source_type=request.source_type,
        state=result.state,
        package_id=request.package_id,
        package_version=request.package_version,
        template_id=request.template_id,
        template_version=request.template_version,
        evidence_type=request.evidence_type,
        query_started_at=request.window.baseline_start,
        query_ended_at=request.window.fault_end,
        query_parameters={key: value for key, value in request.parameters.items()},
        baseline_summary=result.baseline_summary,
        fault_summary=result.fault_summary,
        interpretation=result.interpretation,
        normalized_result=result.normalized_result,
        error_code=result.error_code,
        created_at=now,
    )


def _counts(states: tuple[EvidenceItemState, ...]) -> tuple[int, int, int, int]:
    succeeded = sum(state in {"SUCCEEDED", "NO_DATA"} for state in states)
    skipped = sum(state == "SKIPPED_DEPENDENCY" for state in states)
    missing = sum(state in {"MISSING_TARGET", "INSUFFICIENT_BASELINE"} for state in states)
    failed = sum(state == "FAILED" for state in states)
    return succeeded, skipped, missing, failed


def _append_completion_activity(
    uow: SqlAlchemyUnitOfWork,
    run: EvidenceRun,
    *,
    id_factory: Callable[[IdPrefix], str],
    now: datetime,
) -> None:
    if uow.incidents is None:
        raise RuntimeError("Incident 仓储尚未初始化")
    kind: IncidentActivityKind
    if run.state == "SUCCEEDED":
        kind = "EVIDENCE_COLLECTION_COMPLETED"
    elif run.state == "PARTIAL":
        kind = "EVIDENCE_COLLECTION_PARTIAL"
    else:
        kind = "EVIDENCE_COLLECTION_FAILED"
    summary = {
        "SUCCEEDED": "监控取证已完成",
        "PARTIAL": "监控取证已完成，部分数据源或证据项不可用",
        "FAILED": "监控取证未取得有效证据",
    }[run.state]
    uow.incidents.append_activities(
        (
            IncidentActivity(
                id=id_factory("iact"),
                incident_id=run.incident_id,
                kind=kind,
                occurred_at=now,
                actor_type="SYSTEM",
                actor="evidence-collection",
                summary=summary,
                metadata={
                    "evidence_run_id": run.id,
                    "succeeded_count": run.succeeded_count,
                    "skipped_count": run.skipped_count,
                    "missing_count": run.missing_count,
                    "failed_count": run.failed_count,
                },
            ),
        )
    )


def _completed_result(
    task: EvidenceTaskRecord | None,
    run: EvidenceRun,
) -> EvidenceCollectionResult:
    if task is None:
        raise RuntimeError("evidence_collection_task_not_found")
    return EvidenceCollectionResult(
        task_id=task.id,
        evidence_run_id=run.id,
        outcome=run.state,
        succeeded_count=run.succeeded_count,
        skipped_count=run.skipped_count,
        missing_count=run.missing_count,
        failed_count=run.failed_count,
        replayed=task.state == "SUCCEEDED",
    )


def _error_code(error: Exception) -> str:
    return (str(error).strip() or error.__class__.__name__)[:64]


def _runs(uow: SqlAlchemyUnitOfWork) -> EvidenceRunRepository:
    if uow.evidence_runs is None:
        raise RuntimeError("取证运行仓储尚未初始化")
    return uow.evidence_runs


def _tasks(uow: SqlAlchemyUnitOfWork) -> EvidenceTaskRepository:
    if uow.evidence_tasks is None:
        raise RuntimeError("取证任务仓储尚未初始化")
    return uow.evidence_tasks


def _sources(uow: SqlAlchemyUnitOfWork) -> MonitoringDataSourceRepository:
    if uow.monitoring_data_sources is None:
        raise RuntimeError("监控数据源仓储尚未初始化")
    return uow.monitoring_data_sources
