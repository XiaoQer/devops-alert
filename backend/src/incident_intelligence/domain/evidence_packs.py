from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from incident_intelligence.domain.evidence import EvidenceType, MonitoringSourceType


class EvidenceThreshold(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operator: str = Field(pattern=r"^(GT|GTE|LT|LTE|CHANGE_GTE)$")
    value: float


class EvidenceQueryTemplate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9._-]{0,127}$")
    version: int = Field(ge=1)
    source_type: MonitoringSourceType
    evidence_type: EvidenceType
    display_name: str = Field(min_length=1, max_length=160)
    query_name: str = Field(pattern=r"^[a-z][a-z0-9._-]{0,127}$")
    controlled_query: str = Field(min_length=1, max_length=8_000)
    parameters: tuple[str, ...] = Field(max_length=10)
    threshold: EvidenceThreshold | None = None


class EvidencePack(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    version: int = Field(ge=1)
    priority: int = Field(ge=0, le=100)
    alert_name_patterns: tuple[str, ...] = Field(max_length=20)
    fact_matches: dict[str, tuple[str, ...]] = Field(default_factory=dict, max_length=10)
    templates: tuple[EvidenceQueryTemplate, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_patterns(self) -> EvidencePack:
        for pattern in self.alert_name_patterns:
            re.compile(pattern, re.IGNORECASE)
        return self

    def matches(self, alert_names: tuple[str, ...], facts: Mapping[str, str]) -> bool:
        if any(
            re.search(pattern, alert_name, re.IGNORECASE)
            for pattern in self.alert_name_patterns
            for alert_name in alert_names
        ):
            return True
        return any(
            facts.get(key, "").casefold() in {candidate.casefold() for candidate in values}
            for key, values in self.fact_matches.items()
        )


class EvidencePackRegistry:
    def __init__(self, packs: tuple[EvidencePack, ...]) -> None:
        by_id = {pack.id: pack for pack in packs}
        if len(by_id) != len(packs) or "common-service" not in by_id:
            raise ValueError("invalid_evidence_pack_registry")
        identities: set[tuple[str, str, int]] = set()
        for pack in packs:
            for template in pack.templates:
                identity = (
                    template.source_type,
                    template.query_name,
                    template.version,
                )
                if identity in identities:
                    raise ValueError("duplicate_evidence_template")
                identities.add(identity)
        self._packs = tuple(sorted(packs, key=lambda pack: (pack.priority, pack.id)))
        self._by_id = by_id

    @property
    def packs(self) -> tuple[EvidencePack, ...]:
        return self._packs

    def resolve(
        self,
        alert_names: tuple[str, ...],
        facts: Mapping[str, str],
    ) -> tuple[EvidencePack, ...]:
        selected = [self._by_id["common-service"]]
        selected.extend(
            pack
            for pack in self._packs
            if pack.id != "common-service" and pack.matches(alert_names, facts)
        )
        return tuple(sorted(selected[:5], key=lambda pack: (pack.priority, pack.id)))

    @classmethod
    def default(cls) -> EvidencePackRegistry:
        return cls((_common_pack(), _http_pack(), _jvm_pack(), _mysql_pack()))


def _template(
    template_id: str,
    source_type: MonitoringSourceType,
    evidence_type: EvidenceType,
    display_name: str,
    controlled_query: str,
    *,
    version: int = 1,
    threshold: EvidenceThreshold | None = None,
) -> EvidenceQueryTemplate:
    return EvidenceQueryTemplate(
        id=template_id,
        version=version,
        source_type=source_type,
        evidence_type=evidence_type,
        display_name=display_name,
        query_name=template_id,
        controlled_query=controlled_query,
        parameters=("environment", "service"),
        threshold=threshold,
    )


def _common_pack() -> EvidencePack:
    return EvidencePack(
        id="common-service",
        version=2,
        priority=0,
        alert_name_patterns=(),
        templates=(
            _template(
                "prom.service.availability",
                "PROMETHEUS",
                "METRIC_COMPARISON",
                "服务可用性",
                "avg(up{${environment_matcher}${service_matcher}})",
                version=2,
                threshold=EvidenceThreshold(operator="LT", value=1),
            ),
            _template(
                "prom.service.cpu",
                "PROMETHEUS",
                "METRIC_TIMESERIES",
                "CPU 使用趋势",
                "sum(rate(container_cpu_usage_seconds_total{"
                "${environment_matcher}${service_matcher}}[5m]))",
                version=2,
            ),
            _template(
                "elk.service.errors",
                "ELASTICSEARCH",
                "LOG_AGGREGATION",
                "错误日志趋势",
                "service_error_aggregation",
            ),
            _template(
                "sw.service.health",
                "SKYWALKING",
                "METRIC_COMPARISON",
                "服务链路健康",
                "service_health",
            ),
        ),
    )


def _http_pack() -> EvidencePack:
    return EvidencePack(
        id="http",
        version=2,
        priority=10,
        alert_name_patterns=(r"http", r"latency", r"request", r"5xx", r"errorrate"),
        templates=(
            _template(
                "prom.http.error-rate",
                "PROMETHEUS",
                "METRIC_COMPARISON",
                "HTTP 错误率",
                "sum(rate(http_requests_total{${environment_matcher}${service_matcher},"
                'status=~"5.."}[5m])) / '
                "sum(rate(http_requests_total{${environment_matcher}${service_matcher}}[5m]))",
                version=2,
            ),
            _template(
                "prom.http.latency",
                "PROMETHEUS",
                "METRIC_TIMESERIES",
                "HTTP 延迟",
                "histogram_quantile(0.95,"
                "sum(rate(http_request_duration_seconds_bucket{"
                "${environment_matcher}${service_matcher}}[5m])) by (le))",
                version=2,
            ),
            _template(
                "sw.http.endpoints",
                "SKYWALKING",
                "ENDPOINT_RANKING",
                "异常端点排行",
                "endpoint_ranking",
            ),
            _template(
                "sw.http.failed-traces",
                "SKYWALKING",
                "TRACE_SUMMARY",
                "失败 Trace",
                "failed_traces",
            ),
        ),
    )


def _jvm_pack() -> EvidencePack:
    return EvidencePack(
        id="jvm",
        version=2,
        priority=20,
        alert_name_patterns=(r"jvm", r"gc", r"heap", r"thread"),
        fact_matches={"runtime": ("java", "jvm")},
        templates=(
            _template(
                "prom.jvm.heap",
                "PROMETHEUS",
                "METRIC_TIMESERIES",
                "JVM 堆内存",
                'jvm_memory_used_bytes{${environment_matcher}${service_matcher},area="heap"}',
                version=2,
            ),
            _template(
                "prom.jvm.gc",
                "PROMETHEUS",
                "METRIC_COMPARISON",
                "GC 暂停",
                "rate(jvm_gc_pause_seconds_sum{${environment_matcher}${service_matcher}}[5m])",
                version=2,
            ),
            _template(
                "prom.jvm.threads",
                "PROMETHEUS",
                "METRIC_TIMESERIES",
                "JVM 线程数",
                "jvm_threads_live_threads{${environment_matcher}${service_matcher}}",
                version=2,
            ),
        ),
    )


def _mysql_pack() -> EvidencePack:
    return EvidencePack(
        id="mysql",
        version=2,
        priority=30,
        alert_name_patterns=(r"mysql", r"database", r"rowlock", r"lockwait", r"dbpool"),
        fact_matches={"component": ("mysql",), "database_system": ("mysql",)},
        templates=(
            _template(
                "prom.mysql.pool",
                "PROMETHEUS",
                "METRIC_TIMESERIES",
                "数据库连接池",
                "db_client_connections_usage{${environment_matcher}${service_matcher}}",
                version=2,
            ),
            _template(
                "prom.mysql.row-lock-current-waits",
                "PROMETHEUS",
                "METRIC_TIMESERIES",
                "MySQL 当前行锁等待",
                "mysql_global_status_innodb_row_lock_current_waits"
                "{${environment_matcher}${service_matcher}}",
                version=2,
            ),
            _template(
                "prom.mysql.row-lock-waits-increase",
                "PROMETHEUS",
                "METRIC_COMPARISON",
                "MySQL 行锁等待增量",
                "increase(mysql_global_status_innodb_row_lock_waits"
                "{${environment_matcher}${service_matcher}}[5m])",
                version=2,
            ),
            _template(
                "prom.mysql.row-lock-time-increase",
                "PROMETHEUS",
                "METRIC_COMPARISON",
                "MySQL 行锁等待耗时增量",
                "increase(mysql_global_status_innodb_row_lock_time"
                "{${environment_matcher}${service_matcher}}[5m])",
                version=2,
            ),
            _template(
                "elk.mysql.errors",
                "ELASTICSEARCH",
                "LOG_SAMPLE",
                "数据库错误日志",
                "mysql_error_samples",
            ),
            _template(
                "sw.mysql.dependencies",
                "SKYWALKING",
                "DEPENDENCY_RANKING",
                "数据库依赖耗时",
                "mysql_dependency_ranking",
            ),
        ),
    )
