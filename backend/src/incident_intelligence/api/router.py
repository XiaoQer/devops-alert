from fastapi import APIRouter
from sqlalchemy.engine import Engine

from incident_intelligence.api.routes.alert_groups import router as alert_groups_router
from incident_intelligence.api.routes.alert_sources import router as alert_sources_router
from incident_intelligence.api.routes.alertmanager import router as alertmanager_router
from incident_intelligence.api.routes.alerts import router as alerts_router
from incident_intelligence.api.routes.catalog import router as catalog_router
from incident_intelligence.api.routes.cloudevents import router as cloudevents_router
from incident_intelligence.api.routes.correlation import router as correlation_router
from incident_intelligence.api.routes.health import create_health_router
from incident_intelligence.api.routes.incidents import router as incidents_router
from incident_intelligence.api.routes.manual_reports import router as manual_reports_router
from incident_intelligence.api.routes.resources import create_resources_router


def create_router(engine: Engine) -> APIRouter:
    router = APIRouter()
    router.include_router(create_health_router(engine))
    router.include_router(alert_sources_router)
    router.include_router(alert_groups_router)
    router.include_router(alerts_router)
    router.include_router(alertmanager_router)
    router.include_router(cloudevents_router)
    router.include_router(catalog_router)
    router.include_router(correlation_router)
    router.include_router(incidents_router)
    router.include_router(manual_reports_router)
    router.include_router(create_resources_router(engine))
    return router
