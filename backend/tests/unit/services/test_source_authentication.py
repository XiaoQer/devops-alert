from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import pytest

from incident_intelligence.persistence.models import (
    AlertSourceCredentialRow,
    AlertSourceRow,
)
from incident_intelligence.services.source_authentication import (
    SourceAuthenticationError,
    SourceAuthenticationService,
)

NOW = datetime(2026, 8, 26, 11, 0, tzinfo=UTC)
SOURCE_ID = "src_11111111111111111111111111111111"
CREDENTIAL_ID = "acr_22222222222222222222222222222222"
SECRET = "a" * 43
TOKEN = f"iisrc_{CREDENTIAL_ID}.{SECRET}"


class FakeRepository:
    def __init__(self) -> None:
        self.source = AlertSourceRow(
            id=SOURCE_ID,
            name="测试来源",
            source_type="ALERTMANAGER",
            management_type="USER_MANAGED",
            state="ENABLED",
            version=1,
            last_accepted_at=None,
            last_rejected_at=None,
            last_validated_at=None,
            accepted_requests=0,
            rejected_requests=0,
            opened_count=0,
            updated_count=0,
            resolved_count=0,
            replayed_count=0,
            ignored_count=0,
            created_at=NOW,
            updated_at=NOW,
        )
        self.credential = AlertSourceCredentialRow(
            id=CREDENTIAL_ID,
            alert_source_id=SOURCE_ID,
            token_digest=sha256(TOKEN.encode()).hexdigest(),
            state="ACTIVE",
            created_by="actor",
            last_used_at=None,
            revoked_at=None,
            revoked_by=None,
            created_at=NOW,
        )

    def find_credential_by_id(self, credential_id: str, *, for_update: bool = False):
        del for_update
        return self.credential if credential_id == self.credential.id else None

    def find_source(self, source_id: str, *, for_update: bool = False):
        del for_update
        return self.source if source_id == self.source.id else None

    def flush(self) -> None:
        return None


class FakeUnitOfWork:
    def __init__(self, repository: FakeRepository) -> None:
        self.alert_sources = repository
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def commit(self) -> None:
        self.committed = True


def service_with(repository: FakeRepository) -> SourceAuthenticationService:
    return SourceAuthenticationService(
        uow_factory=lambda: FakeUnitOfWork(repository),  # type: ignore[arg-type]
    )


def test_valid_token_authenticates_and_updates_last_used_time() -> None:
    repository = FakeRepository()
    authenticated = service_with(repository).authenticate(
        SOURCE_ID,
        expected_type="ALERTMANAGER",
        bearer_token=TOKEN,
        now=NOW,
    )
    assert authenticated.alert_source_id == SOURCE_ID
    assert authenticated.credential_id == CREDENTIAL_ID
    assert repository.credential.last_used_at == NOW


@pytest.mark.parametrize(
    ("token", "source_id", "reason_code"),
    [
        ("malformed", SOURCE_ID, "authentication_required"),
        (f"iisrc_{CREDENTIAL_ID}.{'b' * 43}", SOURCE_ID, "authentication_required"),
        (TOKEN, "src_33333333333333333333333333333333", "authentication_required"),
    ],
)
def test_malformed_wrong_and_cross_source_tokens_are_indistinguishable(
    token: str,
    source_id: str,
    reason_code: str,
) -> None:
    with pytest.raises(SourceAuthenticationError) as error:
        service_with(FakeRepository()).authenticate(
            source_id,
            expected_type="ALERTMANAGER",
            bearer_token=token,
            now=NOW,
        )
    assert error.value.reason_code == reason_code
    assert error.value.authenticated_source_id is None


def test_authenticated_disabled_and_type_mismatch_are_explicit() -> None:
    disabled = FakeRepository()
    disabled.source.state = "DISABLED"
    with pytest.raises(SourceAuthenticationError) as disabled_error:
        service_with(disabled).authenticate(
            SOURCE_ID,
            expected_type="ALERTMANAGER",
            bearer_token=TOKEN,
            now=NOW,
        )
    assert disabled_error.value.reason_code == "alert_source_disabled"
    assert disabled_error.value.authenticated_source_id == SOURCE_ID

    mismatch = FakeRepository()
    with pytest.raises(SourceAuthenticationError) as mismatch_error:
        service_with(mismatch).authenticate(
            SOURCE_ID,
            expected_type="CLOUDEVENTS",
            bearer_token=TOKEN,
            now=NOW,
        )
    assert mismatch_error.value.reason_code == "alert_source_type_mismatch"
    assert mismatch_error.value.authenticated_source_id == SOURCE_ID


def test_revoked_credential_is_not_authenticated() -> None:
    repository = FakeRepository()
    repository.credential.state = "REVOKED"
    repository.credential.revoked_at = NOW
    repository.credential.revoked_by = "actor"
    with pytest.raises(SourceAuthenticationError) as error:
        service_with(repository).authenticate(
            SOURCE_ID,
            expected_type="ALERTMANAGER",
            bearer_token=TOKEN,
            now=NOW,
        )
    assert error.value.reason_code == "authentication_required"
    assert error.value.authenticated_source_id is None
