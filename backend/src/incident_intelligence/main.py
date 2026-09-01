import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress

from fastapi import FastAPI
from sqlalchemy.engine import Engine

from incident_intelligence.api.errors import install_error_handlers
from incident_intelligence.api.middleware import RequestBodyLimitMiddleware
from incident_intelligence.api.router import create_router
from incident_intelligence.persistence.session import get_engine, make_session_factory
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_center import AlertCenterService
from incident_intelligence.services.alert_sources import AlertSourceService
from incident_intelligence.services.incident_evaluation import IncidentEvaluationService
from incident_intelligence.services.incident_evaluation_runner import (
    IncidentEvaluationRunner,
)
from incident_intelligence.services.incident_notification_routes import (
    IncidentNotificationRouteService,
)
from incident_intelligence.services.incident_rules import IncidentRuleService
from incident_intelligence.services.incidents import IncidentService
from incident_intelligence.services.signal_intake import SignalIntakeService
from incident_intelligence.services.source_authentication import SourceAuthenticationService
from incident_intelligence.services.source_receipts import SourceReceiptService
from incident_intelligence.settings import Settings

LOGGER = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, *, engine: Engine | None = None) -> FastAPI:
    resolved_settings = settings or Settings()  # type: ignore[call-arg]
    resolved_engine = engine or get_engine(resolved_settings.database_url)
    session_factory = make_session_factory(resolved_engine)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    incident_evaluation_service = IncidentEvaluationService(
        uow_factory=uow_factory,
        owner="incident-evaluation",
        lease_seconds=resolved_settings.incident_worker_lease_seconds,
    )
    incident_evaluation_runner = IncidentEvaluationRunner(
        uow_factory=uow_factory,
        processor=incident_evaluation_service,
        owner="incident-evaluation",
        lease_seconds=resolved_settings.incident_worker_lease_seconds,
        max_attempts=resolved_settings.incident_worker_max_attempts,
    )

    app = FastAPI(
        title="Alert Intake API",
        version="0.2.0",
        lifespan=_lifespan(resolved_settings, incident_evaluation_runner),
    )
    app.state.settings = resolved_settings
    app.state.engine = resolved_engine
    app.state.session_factory = session_factory
    app.state.incident_evaluation_service = incident_evaluation_service
    app.state.incident_evaluation_runner = incident_evaluation_runner
    app.state.alert_source_service = AlertSourceService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.state.alert_center_service = AlertCenterService(session_factory=session_factory)
    app.state.incident_rule_service = IncidentRuleService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.state.incident_notification_route_service = IncidentNotificationRouteService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        capability=resolved_settings.feishu_capability(),
    )
    app.state.incident_service = IncidentService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.state.source_authentication_service = SourceAuthenticationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.state.source_receipt_service = SourceReceiptService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.state.signal_intake_service = SignalIntakeService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        default_max_bytes=resolved_settings.request_body_limit_bytes,
        path_limits={
            "/api/v1/intake/alertmanager": resolved_settings.alertmanager_body_limit_bytes,
            "/api/v1/intake/alertmanager/": resolved_settings.alertmanager_body_limit_bytes,
            "/api/v1/intake/cloudevents": resolved_settings.cloudevents_body_limit_bytes,
            "/api/v1/intake/cloudevents/": resolved_settings.cloudevents_body_limit_bytes,
        },
    )
    install_error_handlers(app)
    app.include_router(create_router(resolved_engine))
    return app


def _lifespan(
    settings: Settings,
    runner: IncidentEvaluationRunner,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        stop = asyncio.Event()
        task: asyncio.Task[None] | None = None
        if settings.incident_workers_enabled:
            task = asyncio.create_task(_run_incident_worker(settings, runner, stop))
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                await task

    return lifespan


async def _run_incident_worker(
    settings: Settings,
    runner: IncidentEvaluationRunner,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(
                runner.run_once,
                limit=settings.incident_worker_batch_size,
            )
        except Exception:
            LOGGER.exception("Incident 评估批次执行失败")
        with suppress(TimeoutError):
            await asyncio.wait_for(
                stop.wait(),
                timeout=settings.incident_worker_poll_seconds,
            )
