from __future__ import annotations

from datetime import UTC, datetime
from functools import partial

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from incident_intelligence.domain.alert_sources import ALERTMANAGER_COMPAT_SOURCE_ID
from incident_intelligence.persistence.models import (
    AlertSourceCredentialRow,
    AlertSourceOperationRow,
    AlertSourceRow,
    AuditEventRow,
)
from incident_intelligence.persistence.unit_of_work import SqlAlchemyUnitOfWork
from incident_intelligence.services.alert_sources import (
    AlertSourceConflict,
    AlertSourceResourceNotFound,
    AlertSourceService,
    AlertSourceVersionConflict,
    CreateAlertSourceCommand,
    LastActiveCredentialError,
    SystemManagedSourceError,
    UpdateAlertSourceCommand,
)

NOW = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
ACTOR = "manual-api-client"


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def service(session_factory: sessionmaker[Session]) -> AlertSourceService:
    return AlertSourceService(
        uow_factory=partial(SqlAlchemyUnitOfWork, session_factory),
        clock=lambda: NOW,
    )


def create_source(service: AlertSourceService, *, key: str = "create-1"):
    return service.create_source(
        CreateAlertSourceCommand(
            name="生产 Alertmanager",
            source_type="ALERTMANAGER",
            environment="production",
            environment_name="生产环境",
        ),
        idempotency_key=key,
        actor=ACTOR,
        request_id="req-create",
    )


def test_create_returns_secret_once_and_persists_only_digest(
    service: AlertSourceService,
    session_factory: sessionmaker[Session],
) -> None:
    created = create_source(service)
    replayed = create_source(service)

    assert created.token is not None
    assert created.token.startswith(f"iisrc_{created.credential_id}.")
    assert created.secret_retrievable is True
    assert created.replayed is False
    assert replayed.token is None
    assert replayed.secret_retrievable is False
    assert replayed.replayed is True
    assert replayed.source.id == created.source.id

    with session_factory() as session:
        credential = session.get(AlertSourceCredentialRow, created.credential_id)
        operation = session.scalar(select(AlertSourceOperationRow))
        persisted = " ".join(
            str(value)
            for row_type in (
                AlertSourceRow,
                AlertSourceCredentialRow,
                AlertSourceOperationRow,
                AuditEventRow,
            )
            for row in session.scalars(select(row_type))
            for value in vars(row).values()
        )
    assert credential is not None
    assert len(credential.token_digest) == 64
    assert created.token not in persisted
    assert operation is not None
    assert operation.idempotency_key_hash != "create-1"


def test_update_rotate_and_revoke_enforce_versions_and_last_active_guard(
    service: AlertSourceService,
    session_factory: sessionmaker[Session],
) -> None:
    created = create_source(service)
    source_id = created.source.id

    with pytest.raises(AlertSourceVersionConflict):
        service.update_source(
            source_id,
            UpdateAlertSourceCommand(expected_version=99, name="新名称"),
            idempotency_key="stale-update",
            actor=ACTOR,
            request_id="req-stale",
        )

    with pytest.raises(LastActiveCredentialError):
        service.revoke_credential(
            source_id,
            created.credential_id,
            expected_version=1,
            idempotency_key="revoke-last",
            actor=ACTOR,
            request_id="req-revoke-last",
        )

    rotated = service.rotate_credential(
        source_id,
        expected_version=1,
        idempotency_key="rotate-1",
        actor=ACTOR,
        request_id="req-rotate",
    )
    assert rotated.token is not None
    assert rotated.source.version == 2

    revoked = service.revoke_credential(
        source_id,
        created.credential_id,
        expected_version=2,
        idempotency_key="revoke-old",
        actor=ACTOR,
        request_id="req-revoke-old",
    )
    assert revoked.source.version == 3
    assert revoked.token is None
    with session_factory() as session:
        old = session.get(AlertSourceCredentialRow, created.credential_id)
        active_count = session.scalar(
            select(func.count())
            .select_from(AlertSourceCredentialRow)
            .where(
                AlertSourceCredentialRow.alert_source_id == source_id,
                AlertSourceCredentialRow.state == "ACTIVE",
            )
        )
    assert old is not None and old.state == "REVOKED"
    assert active_count == 1


def test_disable_allows_revoking_last_credential(service: AlertSourceService) -> None:
    created = create_source(service)
    disabled = service.update_source(
        created.source.id,
        UpdateAlertSourceCommand(expected_version=1, state="DISABLED"),
        idempotency_key="disable-1",
        actor=ACTOR,
        request_id="req-disable",
    )
    revoked = service.revoke_credential(
        created.source.id,
        created.credential_id,
        expected_version=disabled.source.version,
        idempotency_key="revoke-disabled",
        actor=ACTOR,
        request_id="req-revoke",
    )
    assert revoked.source.state == "DISABLED"


def test_system_source_is_read_only_and_resources_are_bounded(service: AlertSourceService) -> None:
    with pytest.raises(SystemManagedSourceError):
        service.update_source(
            ALERTMANAGER_COMPAT_SOURCE_ID,
            UpdateAlertSourceCommand(expected_version=1, name="禁止修改"),
            idempotency_key="system-update",
            actor=ACTOR,
            request_id="req-system",
        )
    with pytest.raises(AlertSourceResourceNotFound):
        service.get_source("src_ffffffffffffffffffffffffffffffff")


def test_name_and_idempotency_conflicts_are_explicit(service: AlertSourceService) -> None:
    create_source(service)
    with pytest.raises(AlertSourceConflict):
        create_source(service, key="different-key")
    with pytest.raises(AlertSourceConflict) as conflict:
        service.create_source(
            CreateAlertSourceCommand(
                name="另一个来源",
                source_type="CLOUDEVENTS",
                environment="development",
                environment_name="开发环境",
            ),
            idempotency_key="create-1",
            actor=ACTOR,
            request_id="req-conflict",
        )
    assert conflict.value.reason_code == "idempotency_conflict"
