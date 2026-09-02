from __future__ import annotations

from incident_intelligence.adapters.elasticsearch import ElasticsearchEvidenceAdapter
from incident_intelligence.adapters.monitoring_http import (
    MonitoringHttpTransport,
    MonitoringPermanentError,
    UrllibMonitoringTransport,
)
from incident_intelligence.adapters.prometheus import PrometheusEvidenceAdapter
from incident_intelligence.adapters.skywalking import SkyWalkingEvidenceAdapter
from incident_intelligence.persistence.evidence_repository import MonitoringDataSourceRecord
from incident_intelligence.services.evidence_collection import EvidenceAdapter
from incident_intelligence.services.monitoring_data_sources import (
    MonitoringCredentialResolver,
    MonitoringDataSource,
)


class MonitoringEvidenceAdapterFactory:
    def __init__(
        self,
        *,
        credential_resolver: MonitoringCredentialResolver | None = None,
        transport: MonitoringHttpTransport | None = None,
    ) -> None:
        self._credential_resolver = credential_resolver or MonitoringCredentialResolver()
        self._transport = transport or UrllibMonitoringTransport()

    def create(self, record: MonitoringDataSourceRecord) -> EvidenceAdapter:
        source = MonitoringDataSource.model_validate(record.__dict__)
        credential = self._credential_resolver.resolve(source)
        if source.credential_env_key is not None and credential is None:
            raise MonitoringPermanentError("monitoring_credential_missing")
        if source.source_type == "PROMETHEUS":
            return PrometheusEvidenceAdapter(
                base_url=source.base_url,
                transport=self._transport,
                credential=credential,
            )
        if source.source_type == "ELASTICSEARCH":
            index = source.field_mapping.get("index")
            if index is None:
                raise MonitoringPermanentError("elasticsearch_index_missing")
            return ElasticsearchEvidenceAdapter(
                base_url=source.base_url,
                index=index,
                field_mapping={
                    key: value for key, value in source.field_mapping.items() if key != "index"
                },
                transport=self._transport,
                credential=credential,
            )
        graphql_path = source.field_mapping.get("graphql_path", "/graphql")
        return SkyWalkingEvidenceAdapter(
            base_url=source.base_url,
            graphql_path=graphql_path,
            transport=self._transport,
            credential=credential,
        )
