from __future__ import annotations

import json
import re

from incident_intelligence.adapters.monitoring_http import (
    AdapterEvidenceResult,
    MonitoringHttpTransport,
    MonitoringPermanentError,
)
from incident_intelligence.services.evidence_normalization import (
    log_fingerprint,
    normalize_log_message,
)
from incident_intelligence.services.evidence_planning import EvidenceQueryRequest

_SAFE_INDEX = re.compile(r"^[a-zA-Z0-9._*-]{1,255}$")
_SAFE_FIELD = re.compile(r"^[a-zA-Z0-9_.@-]{1,256}$")
_SOURCE_KEYS = (
    "timestamp",
    "service",
    "environment",
    "level",
    "message",
    "error_type",
    "trace_id",
    "host",
)


class ElasticsearchEvidenceAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        index: str,
        field_mapping: dict[str, str],
        transport: MonitoringHttpTransport,
        credential: str | None = None,
        timeout_seconds: float = 10,
        max_response_bytes: int = 3_000_000,
    ) -> None:
        if not _SAFE_INDEX.fullmatch(index):
            raise MonitoringPermanentError("elasticsearch_index_invalid")
        selected_fields = {key: field_mapping[key] for key in _SOURCE_KEYS if key in field_mapping}
        if not all(_SAFE_FIELD.fullmatch(value) for value in selected_fields.values()):
            raise MonitoringPermanentError("elasticsearch_field_invalid")
        required = {"timestamp", "service", "environment", "message"}
        if not required.issubset(selected_fields):
            raise MonitoringPermanentError("elasticsearch_mapping_incomplete")
        self._base_url = base_url.rstrip("/")
        self._index = index
        self._fields = selected_fields
        self._transport = transport
        self._credential = credential
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    def collect(self, request: EvidenceQueryRequest) -> AdapterEvidenceResult:
        if request.source_type != "ELASTICSEARCH":
            raise MonitoringPermanentError("elasticsearch_request_type_invalid")
        service = request.parameters.get("service")
        environment = request.parameters.get("environment")
        if request.pre_result_state == "MISSING_TARGET" or service is None:
            return AdapterEvidenceResult(
                state="MISSING_TARGET",
                interpretation="缺少服务标识。未执行 Elasticsearch 查询。",
            )
        if environment is None:
            raise MonitoringPermanentError("elasticsearch_environment_missing")
        headers = {"Accept": "application/json"}
        if self._credential is not None:
            headers["Authorization"] = f"Bearer {self._credential}"
        response = self._transport.request(
            "POST",
            f"{self._base_url}/{self._index}/_search",
            headers=headers,
            form=None,
            json=self._build_query(request, service=service, environment=environment),
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
        )
        return self._parse(response.body)

    def _build_query(
        self,
        request: EvidenceQueryRequest,
        *,
        service: str,
        environment: str,
    ) -> dict[str, object]:
        timestamp_field = self._fields["timestamp"]
        return {
            "size": 50,
            "track_total_hits": True,
            "_source": list(self._fields.values()),
            "query": {
                "bool": {
                    "filter": [
                        {
                            "range": {
                                timestamp_field: {
                                    "gte": request.window.baseline_start.isoformat(),
                                    "lt": request.window.fault_end.isoformat(),
                                }
                            }
                        },
                        {"term": {self._fields["service"]: service}},
                        {"term": {self._fields["environment"]: environment}},
                    ]
                }
            },
            "sort": [{timestamp_field: "desc"}],
        }

    def _parse(self, body: bytes) -> AdapterEvidenceResult:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MonitoringPermanentError("invalid_json_response") from None
        if not isinstance(payload, dict):
            raise MonitoringPermanentError("elasticsearch_response_invalid")
        hits_block = payload.get("hits")
        if not isinstance(hits_block, dict) or not isinstance(hits_block.get("hits"), list):
            raise MonitoringPermanentError("elasticsearch_response_invalid")
        raw_hits = hits_block["hits"]
        samples = [self._normalize_hit(hit) for hit in raw_hits[:50]]
        samples = [sample for sample in samples if sample is not None]
        if not samples:
            return AdapterEvidenceResult(
                state="NO_DATA",
                normalized_result={"samples": [], "total": 0, "truncated": False},
                interpretation="查询成功。该时间窗口没有匹配日志。",
            )
        total = _total_hits(hits_block.get("total"), len(raw_hits))
        return AdapterEvidenceResult(
            state="SUCCEEDED",
            normalized_result={
                "samples": samples,
                "total": total,
                "truncated": len(raw_hits) > 50 or total > 50,
            },
            fault_summary={"log_count": total, "sample_count": len(samples)},
            interpretation=f"已找到 {total} 条匹配日志。保留 {len(samples)} 条代表样本。",
        )

    def _normalize_hit(self, hit: object) -> dict[str, str] | None:
        if not isinstance(hit, dict) or not isinstance(hit.get("_source"), dict):
            return None
        source = hit["_source"]
        message = normalize_log_message(str(_read_field(source, self._fields["message"]) or ""))
        error_type = str(_read_field(source, self._fields.get("error_type", "")) or "")[:256]
        return {
            "timestamp": str(_read_field(source, self._fields["timestamp"]) or "")[:64],
            "level": str(_read_field(source, self._fields.get("level", "")) or "")[:32],
            "message": message,
            "error_type": error_type,
            "trace_id": str(_read_field(source, self._fields.get("trace_id", "")) or "")[:128],
            "host": str(_read_field(source, self._fields.get("host", "")) or "")[:256],
            "fingerprint": log_fingerprint(error_type, message),
        }


def _read_field(source: dict[str, object], field: str) -> object | None:
    if not field:
        return None
    if field in source:
        return source[field]
    current: object = source
    for segment in field.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _total_hits(value: object, fallback: int) -> int:
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, dict):
        raw_count = value.get("value")
        if isinstance(raw_count, int):
            return max(0, raw_count)
    return fallback
