from fastapi import FastAPI
from sqlalchemy.engine import Engine

from incident_intelligence.api.errors import install_error_handlers
from incident_intelligence.api.middleware import RequestBodyLimitMiddleware
from incident_intelligence.api.router import create_router
from incident_intelligence.persistence.session import get_engine, make_session_factory
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.manual_intake import ManualIntakeService
from incident_intelligence.settings import Settings


def create_app(settings: Settings | None = None, *, engine: Engine | None = None) -> FastAPI:
    resolved_settings = settings or Settings()  # type: ignore[call-arg]
    resolved_engine = engine or get_engine(resolved_settings.database_url)
    session_factory = make_session_factory(resolved_engine)

    app = FastAPI(title="Incident Intelligence API", version="0.1.0")
    app.state.settings = resolved_settings
    app.state.engine = resolved_engine
    app.state.manual_intake_service = ManualIntakeService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=resolved_settings.request_body_limit_bytes,
    )
    install_error_handlers(app)
    app.include_router(create_router(resolved_engine))
    return app
