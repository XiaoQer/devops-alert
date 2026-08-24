from fastapi import APIRouter
from sqlalchemy.engine import Engine

from incident_intelligence.api.routes.health import create_health_router


def create_router(engine: Engine) -> APIRouter:
    router = APIRouter()
    router.include_router(create_health_router(engine))
    return router
