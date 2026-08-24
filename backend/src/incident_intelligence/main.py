from fastapi import FastAPI
from sqlalchemy.engine import Engine

from incident_intelligence.api.router import create_router
from incident_intelligence.persistence.session import get_engine
from incident_intelligence.settings import Settings


def create_app(settings: Settings | None = None, *, engine: Engine | None = None) -> FastAPI:
    resolved_settings = settings or Settings()  # type: ignore[call-arg]
    resolved_engine = engine or get_engine(resolved_settings.database_url)

    app = FastAPI(title="Incident Intelligence API", version="0.1.0")
    app.state.settings = resolved_settings
    app.state.engine = resolved_engine
    app.include_router(create_router(resolved_engine))
    return app
