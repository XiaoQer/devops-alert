from fastapi import APIRouter
from sqlalchemy.engine import Engine

from incident_intelligence.api.routes.alert_sources import router as alert_sources_router
from incident_intelligence.api.routes.alertmanager import router as alertmanager_router
from incident_intelligence.api.routes.alerts import router as alerts_router
from incident_intelligence.api.routes.cloudevents import router as cloudevents_router
from incident_intelligence.api.routes.feishu import router as feishu_router
from incident_intelligence.api.routes.health import create_health_router
from incident_intelligence.api.routes.incident_notification_routes import (
    router as incident_notification_routes_router,
)
from incident_intelligence.api.routes.incident_rules import router as incident_rules_router
from incident_intelligence.api.routes.incidents import router as incidents_router
from incident_intelligence.api.routes.monitoring_data_sources import (
    router as monitoring_data_sources_router,
)


def create_router(engine: Engine) -> APIRouter:
    router = APIRouter()
    router.include_router(create_health_router(engine))
    router.include_router(alert_sources_router)
    router.include_router(alerts_router)
    router.include_router(incident_rules_router)
    router.include_router(incident_notification_routes_router)
    router.include_router(incidents_router)
    router.include_router(monitoring_data_sources_router)
    router.include_router(feishu_router)
    router.include_router(alertmanager_router)
    router.include_router(cloudevents_router)
    return router
