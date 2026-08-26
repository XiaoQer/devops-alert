from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from secrets import compare_digest
from typing import cast

from incident_intelligence.domain.alert_sources import AlertSourceType
from incident_intelligence.persistence.alert_source_repository import AlertSourceRepository
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork

TOKEN_PATTERN = re.compile(r"^iisrc_(acr_[0-9a-f]{32})\.([A-Za-z0-9_-]{43,})$")


@dataclass(frozen=True, slots=True)
class AuthenticatedAlertSource:
    alert_source_id: str
    source_type: AlertSourceType
    credential_id: str
    actor: str


@dataclass(frozen=True, slots=True)
class SourceAuthenticationError(Exception):
    reason_code: str = "authentication_required"
    authenticated_source_id: str | None = None


class SourceAuthenticationService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    ) -> None:
        self._uow_factory = uow_factory

    def authenticate(
        self,
        alert_source_id: str,
        *,
        expected_type: AlertSourceType,
        bearer_token: str,
        now: datetime,
    ) -> AuthenticatedAlertSource:
        parsed = TOKEN_PATTERN.fullmatch(bearer_token)
        if parsed is None:
            raise SourceAuthenticationError()
        credential_id = parsed.group(1)

        with self._uow_factory() as uow:
            sources = _sources(uow)
            credential = sources.find_credential_by_id(credential_id, for_update=True)
            supplied_digest = sha256(bearer_token.encode()).hexdigest()
            valid_credential = (
                credential is not None
                and credential.state == "ACTIVE"
                and compare_digest(supplied_digest, credential.token_digest)
                and credential.alert_source_id == alert_source_id
            )
            if not valid_credential or credential is None:
                raise SourceAuthenticationError()

            source = sources.find_source(alert_source_id, for_update=True)
            if source is None:
                raise SourceAuthenticationError()
            if source.source_type != expected_type:
                raise SourceAuthenticationError(
                    "alert_source_type_mismatch",
                    authenticated_source_id=source.id,
                )
            if source.state != "ENABLED":
                raise SourceAuthenticationError(
                    "alert_source_disabled",
                    authenticated_source_id=source.id,
                )

            used_at = now.astimezone(UTC)
            credential.last_used_at = used_at
            sources.flush()
            uow.commit()
            return AuthenticatedAlertSource(
                alert_source_id=source.id,
                source_type=cast(AlertSourceType, source.source_type),
                credential_id=credential.id,
                actor=f"alert-source:{source.id}",
            )


def _sources(uow: SqlAlchemyUnitOfWork) -> AlertSourceRepository:
    if uow.alert_sources is None:
        raise RuntimeError("工作单元没有可用告警源仓储")
    return uow.alert_sources
