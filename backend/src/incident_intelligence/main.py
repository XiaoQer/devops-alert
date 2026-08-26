import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.engine import Engine

from incident_intelligence.api.errors import install_error_handlers
from incident_intelligence.api.middleware import RequestBodyLimitMiddleware
from incident_intelligence.api.router import create_router
from incident_intelligence.persistence.session import get_engine, make_session_factory
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_center import AlertCenterService
from incident_intelligence.services.alert_sources import AlertSourceService
from incident_intelligence.services.catalog import ServiceCatalogService
from incident_intelligence.services.correlation import CorrelationReadService, CorrelationService
from incident_intelligence.services.correlation_jobs import CorrelationJobService
from incident_intelligence.services.correlation_runner import CorrelationRunner
from incident_intelligence.services.incident_center import IncidentCenterService
from incident_intelligence.services.incident_operations import IncidentOperationService
from incident_intelligence.services.manual_intake import ManualIntakeService
from incident_intelligence.services.signal_intake import SignalIntakeService
from incident_intelligence.services.source_authentication import SourceAuthenticationService
from incident_intelligence.services.source_receipts import SourceReceiptService
from incident_intelligence.settings import Settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    runner_task: asyncio.Task[None] | None = None
    if app.state.settings.correlation_runner_enabled:
        runner_task = asyncio.create_task(app.state.correlation_runner.run_forever())
    try:
        yield
    finally:
        if runner_task is not None:
            await app.state.correlation_runner.stop()
            await runner_task


def create_app(settings: Settings | None = None, *, engine: Engine | None = None) -> FastAPI:
    resolved_settings = settings or Settings()  # type: ignore[call-arg]
    resolved_engine = engine or get_engine(resolved_settings.database_url)
    session_factory = make_session_factory(resolved_engine)

    app = FastAPI(
        title="Incident Intelligence API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.engine = resolved_engine
    app.state.session_factory = session_factory
    app.state.manual_intake_service = ManualIntakeService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.state.alert_source_service = AlertSourceService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.state.alert_center_service = AlertCenterService(session_factory=session_factory)
    app.state.source_authentication_service = SourceAuthenticationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.state.source_receipt_service = SourceReceiptService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.state.signal_intake_service = SignalIntakeService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.state.catalog_service = ServiceCatalogService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.state.correlation_service = CorrelationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.state.correlation_read_service = CorrelationReadService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.state.correlation_job_service = CorrelationJobService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.state.incident_center_service = IncidentCenterService(session_factory=session_factory)
    app.state.incident_operation_service = IncidentOperationService(session_factory=session_factory)
    app.state.correlation_runner = CorrelationRunner(
        job_service=app.state.correlation_job_service,
        processor=app.state.correlation_service,
        settings=resolved_settings,
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
