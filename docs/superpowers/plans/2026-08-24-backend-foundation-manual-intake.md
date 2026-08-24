# Backend Foundation and Manual Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first runnable backend slice in which an authenticated, bounded manual incident report is stored idempotently as a `SignalEvent`, projected to an `Alert`, opened as an `Incident`, and queued as a `DiagnosisRun`, with health and read APIs.

**Architecture:** A synchronous FastAPI controller uses Pydantic boundary models, a pure domain package for independent states and transitions, and SQLAlchemy repositories inside one PostgreSQL transaction. Manual intake always creates a standalone incident in this phase; correlation, external adapters, evidence collection, AI analysis, and the Vue UI remain outside this plan.

**Tech Stack:** Python 3.13–3.14, FastAPI, Pydantic, pydantic-settings, SQLAlchemy 2, Alembic, PostgreSQL 16, psycopg 3, Pytest, Ruff, Mypy, Docker Compose.

**Spec:** `specs/active/multi-source-incident-center.md` and `docs/superpowers/specs/2026-08-24-multi-source-incident-center-design.md`

## Global Constraints

- Work only in `/Users/shaoqian.li/Documents/incident-intelligence`; do not import or copy code, data, migrations, or internal contracts from the fault-injection repository.
- Do not create a frontend in this plan.
- Do not accept or persist `scenario_id`, `scenario_version`, `experiment_id`, injection actions, or ground truth in any spelling or nesting depth.
- AI does not query monitoring systems, and this phase contains no AI provider or monitoring connector.
- `SignalEvent`, `Alert`, `Incident`, and `DiagnosisRun` remain separate persisted models.
- Alert, incident, and diagnosis states remain separate enums and transition policies.
- External payloads are authenticated, schema-validated, size-limited, and rejected on unknown fields.
- Secrets are loaded from environment variables and never returned by APIs, logged, committed, or inserted into fixtures.
- Python support is `>=3.13,<3.15`; exact direct and transitive dependencies are frozen in `backend/requirements.lock` during Task 1.
- PostgreSQL 16 is the only supported persistence engine; SQLite is not used as a test substitute.
- All timestamps are timezone-aware UTC values.
- Phase 1 uses synchronous SQLAlchemy sessions and synchronous FastAPI route functions to keep transaction ownership explicit.
- API error bodies use `{"code": "stable_reason_code", "message": "中文说明", "request_id": "..."}` and never echo raw request bodies.

## Phase Boundary

This plan implements one independently testable vertical slice:

1. liveness and database readiness;
2. four domain records and three independent state families;
3. the initial PostgreSQL schema and append-only audit records;
4. authenticated manual intake with idempotency and forbidden-identity rejection;
5. read APIs for the four created records.

The following require later plans: Alertmanager and CloudEvents adapters, service catalog, correlation rules, merge/split, evidence queries, Worker registration, task leasing, diagnosis execution, AI analysis, recovery workflow, and Vue UI.

## File Map

```text
backend/
  pyproject.toml                    Python metadata and direct dependency constraints
  requirements.lock                Fully resolved production and development dependencies
  alembic.ini                       Alembic configuration
  src/incident_intelligence/
    __init__.py
    main.py                         FastAPI application factory
    settings.py                     Environment-only configuration
    api/
      dependencies.py               Settings, authentication, and DB session dependencies
      errors.py                     Stable API error envelope and handlers
      router.py                     Versioned API composition
      routes/health.py              Liveness and readiness
      routes/manual_reports.py      Manual intake endpoint
      routes/resources.py           Four read endpoints
      schemas/manual_reports.py     Bounded request and response contracts
      schemas/resources.py          Read response contracts
    domain/
      enums.py                      Independent alert, incident, and diagnosis states
      models.py                     Immutable domain records
      transitions.py                Explicit state transition validation
      forbidden_identity.py         Recursive experiment-identity rejection
    persistence/
      base.py                       Declarative base and naming convention
      models.py                     SQLAlchemy persistence models
      session.py                    Engine/session construction
      repositories.py              Focused record repositories
      unit_of_work.py               Transaction boundary
    services/
      manual_intake.py              Atomic manual-report use case
    ids.py                          Prefixed UUID identifiers
  migrations/
    env.py
    versions/0001_initial_domain.py
  tests/
    conftest.py                     PostgreSQL lifecycle and app fixtures
    unit/domain/
    integration/persistence/
    api/
compose.yaml                        Local PostgreSQL only
scripts/verify-backend.sh           One backend verification entrypoint
```

---

### Task 1: Runnable Backend Skeleton and Health Contract

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/requirements.lock`
- Create: `backend/src/incident_intelligence/__init__.py`
- Create: `backend/src/incident_intelligence/settings.py`
- Create: `backend/src/incident_intelligence/main.py`
- Create: `backend/src/incident_intelligence/api/router.py`
- Create: `backend/src/incident_intelligence/api/routes/health.py`
- Create: `backend/src/incident_intelligence/persistence/base.py`
- Create: `backend/src/incident_intelligence/persistence/session.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/api/test_health.py`
- Create: `compose.yaml`
- Create: `scripts/verify-backend.sh`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `create_app(settings: Settings | None = None) -> FastAPI`
- Produces: `Settings(database_url: str, api_token: SecretStr, request_body_limit_bytes: int)`
- Produces: `GET /health/live` and `GET /health/ready`
- Produces: `get_engine(database_url: str) -> Engine` and `make_session_factory(engine: Engine) -> sessionmaker[Session]`

- [x] **Step 1: Write the failing health tests**

```python
def test_liveness_does_not_require_database(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_reports_database_connection(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "available"}
```

- [x] **Step 2: Run the test to verify the missing application fails**

Run: `cd backend && python -m pytest tests/api/test_health.py -q`

Expected: FAIL during import because `incident_intelligence.main` does not exist.

- [x] **Step 3: Add the package metadata and exact dependency lock**

Define these direct constraints in `backend/pyproject.toml`:

```toml
[project]
name = "incident-intelligence"
version = "0.1.0"
requires-python = ">=3.13,<3.15"
dependencies = [
  "alembic>=1.16,<2",
  "fastapi>=0.116,<1",
  "psycopg[binary]>=3.2,<4",
  "pydantic>=2.11,<3",
  "pydantic-settings>=2.10,<3",
  "sqlalchemy>=2.0.40,<3",
  "uvicorn[standard]>=0.35,<1",
]

[project.optional-dependencies]
dev = [
  "httpx2>=2,<3",
  "mypy>=1.17,<2",
  "pip-tools>=7.5,<8",
  "pytest>=8.4,<9",
  "pytest-cov>=6.2,<7",
  "ruff>=0.12,<1",
]
```

Create the lock from a clean Python 3.13 or 3.14 environment:

```bash
python -m pip install --upgrade "pip-tools>=7.5,<8"
python -m piptools compile --all-extras --strip-extras --output-file requirements.lock pyproject.toml
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
```

- [x] **Step 4: Implement settings, application factory, and health routes**

Use environment prefix `II_`, require `II_DATABASE_URL` and `II_API_TOKEN`, keep the token as `SecretStr`, and default `request_body_limit_bytes` to `65536`. `GET /health/live` returns without touching external state. `GET /health/ready` executes `SELECT 1`; a database failure returns HTTP 503 with `{"status":"not_ready","database":"unavailable"}`.

- [x] **Step 5: Add the PostgreSQL test fixture and local service**

Define only a PostgreSQL 16 service in `compose.yaml`. The test fixture reads `II_TEST_DATABASE_URL`, refuses non-PostgreSQL URLs, creates a fresh schema per test session, and drops only that schema during teardown. It must not use a broad database-drop command.

- [x] **Step 6: Add the unified backend verifier**

`scripts/verify-backend.sh` runs, in this order:

```bash
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy src
python -m pytest --cov=incident_intelligence --cov-report=term-missing --cov-fail-under=90
```

- [x] **Step 7: Run and pass the focused tests**

Run: `cd backend && python -m pytest tests/api/test_health.py -q`

Expected: 3 tests pass: liveness, readiness available, readiness unavailable.

- [x] **Step 8: Commit the runnable skeleton**

```bash
git add .gitignore backend compose.yaml scripts/verify-backend.sh
git commit -m "feat: establish backend runtime and health checks"
```

---

### Task 2: Independent Domain Models and State Policies

**Files:**
- Create: `backend/src/incident_intelligence/domain/enums.py`
- Create: `backend/src/incident_intelligence/domain/models.py`
- Create: `backend/src/incident_intelligence/domain/transitions.py`
- Create: `backend/src/incident_intelligence/domain/forbidden_identity.py`
- Create: `backend/src/incident_intelligence/ids.py`
- Create: `backend/tests/unit/domain/test_models.py`
- Create: `backend/tests/unit/domain/test_transitions.py`
- Create: `backend/tests/unit/domain/test_forbidden_identity.py`

**Interfaces:**
- Produces: `AlertState`, `IncidentState`, and `DiagnosisState` as separate `StrEnum` types
- Produces: immutable `SignalEvent`, `Alert`, `Incident`, and `DiagnosisRun` Pydantic domain records
- Produces: `require_alert_transition`, `require_incident_transition`, and `require_diagnosis_transition`
- Produces: `reject_forbidden_identity(value: object) -> None`
- Produces: `new_id(prefix: Literal["sig", "alt", "inc", "diag", "aud"]) -> str`

- [x] **Step 1: Write failing tests for independent states and immutable records**

```python
def test_state_families_are_not_interchangeable():
    assert AlertState.ACTIVE != IncidentState.DETECTED
    with pytest.raises(ValueError):
        Incident.model_validate({**incident_payload, "state": "ACTIVE"})


def test_signal_event_is_immutable():
    signal = SignalEvent.model_validate(signal_payload)
    with pytest.raises(ValidationError):
        signal.title = "changed"
```

- [x] **Step 2: Run the model tests and observe import failure**

Run: `cd backend && python -m pytest tests/unit/domain/test_models.py -q`

Expected: FAIL because the domain package does not exist.

- [x] **Step 3: Define the exact initial states and transitions**

```python
class AlertState(StrEnum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    SUPPRESSED = "SUPPRESSED"


class IncidentState(StrEnum):
    DETECTED = "DETECTED"
    TRIAGING = "TRIAGING"
    INVESTIGATING = "INVESTIGATING"
    MITIGATING = "MITIGATING"
    MONITORING_RECOVERY = "MONITORING_RECOVERY"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class DiagnosisState(StrEnum):
    QUEUED = "QUEUED"
    COLLECTING = "COLLECTING"
    NORMALIZING = "NORMALIZING"
    SNAPSHOT_READY = "SNAPSHOT_READY"
    ANALYZING = "ANALYZING"
    REPORT_READY = "REPORT_READY"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
```

Allow only documented forward transitions; terminal states have no automatic exit. This task tests policies only and does not expose transition APIs.

- [x] **Step 4: Write failing recursive forbidden-identity tests**

```python
@pytest.mark.parametrize(
    "payload",
    [
        {"scenario_id": "s1"},
        {"labels": {"Scenario-Version": "v1"}},
        {"context": [{"experimentId": "e1"}]},
        {"metadata": {"injection_action": "cpu"}},
        {"facts": {"groundTruth": "answer"}},
    ],
)
def test_experiment_identity_is_rejected_at_any_depth(payload):
    with pytest.raises(ForbiddenIdentityError):
        reject_forbidden_identity(payload)
```

- [x] **Step 5: Implement normalized-key rejection**

Normalize keys by Unicode case-folding and removing `-`, `_`, spaces, and punctuation. Reject normalized keys `scenarioid`, `scenarioversion`, `experimentid`, `injectionaction`, and `groundtruth` anywhere in mappings or sequences. The exception exposes only a stable reason code and the rejected key name, never its value.

- [x] **Step 6: Implement immutable records and prefixed identifiers**

Each record uses `ConfigDict(frozen=True, extra="forbid")`, a prefixed UUID identifier, `created_at`, and its own state type. `SignalEvent` additionally stores `source`, `source_event_id`, `observed_at`, `received_at`, bounded normalized facts, and a SHA-256 payload fingerprint. No record contains an experiment-related field.

- [x] **Step 7: Run all domain tests**

Run: `cd backend && python -m pytest tests/unit/domain -q`

Expected: all domain model, transition, identifier, and forbidden-identity tests pass.

- [x] **Step 8: Commit the domain layer**

```bash
git add backend/src/incident_intelligence/domain backend/src/incident_intelligence/ids.py backend/tests/unit/domain
git commit -m "feat: define incident domain boundaries"
```

---

### Task 3: PostgreSQL Schema, Migration, and Audit Trail

**Files:**
- Create: `backend/alembic.ini`
- Create: `backend/migrations/env.py`
- Create: `backend/migrations/versions/0001_initial_domain.py`
- Create: `backend/src/incident_intelligence/persistence/models.py`
- Create: `backend/tests/integration/persistence/test_initial_migration.py`
- Create: `backend/tests/integration/persistence/test_constraints.py`

**Interfaces:**
- Produces: tables `signal_events`, `alerts`, `incidents`, `diagnosis_runs`, `ingestion_keys`, and `audit_events`
- Produces: one-way foreign keys `alerts.signal_event_id`, `incidents.primary_alert_id`, and `diagnosis_runs.incident_id`
- Produces: append-only `audit_events` records with actor, action, resource type/id, request ID, and UTC timestamp

- [x] **Step 1: Write a failing migration test**

```python
def test_upgrade_creates_domain_tables(postgres_connection):
    command.upgrade(alembic_config, "head")
    names = set(inspect(postgres_connection).get_table_names())
    assert {
        "signal_events",
        "alerts",
        "incidents",
        "diagnosis_runs",
        "ingestion_keys",
        "audit_events",
    } <= names
```

- [x] **Step 2: Run it before creating the migration**

Run: `cd backend && python -m pytest tests/integration/persistence/test_initial_migration.py -q`

Expected: FAIL because Alembic configuration and migration do not exist.

- [x] **Step 3: Implement SQLAlchemy tables and the explicit migration**

Use native PostgreSQL `JSONB` only for bounded `facts` and audit details. Add these database-enforced rules:

- unique `(source, source_event_id)` on `signal_events`;
- unique `(scope, idempotency_key)` on `ingestion_keys`;
- check constraints for each table's own state values;
- maximum lengths on all text-facing columns;
- indexes on `observed_at`, alert state, incident state, diagnosis state, and foreign keys;
- no cascade delete from a parent record to immutable history.

- [x] **Step 4: Add constraint tests**

Test duplicate source identity, cross-family state values, missing relationships, and oversized values at the database boundary. Expected failures are `IntegrityError`, followed by explicit session rollback.

- [x] **Step 5: Verify upgrade and downgrade in a disposable schema**

Run:

```bash
cd backend
python -m alembic upgrade head
python -m alembic downgrade base
python -m alembic upgrade head
python -m pytest tests/integration/persistence -q
```

Expected: migration round-trip succeeds and all persistence integration tests pass.

- [x] **Step 6: Commit the schema**

```bash
git add backend/alembic.ini backend/migrations backend/src/incident_intelligence/persistence/models.py backend/tests/integration/persistence
git commit -m "feat: persist incident domain records"
```

---

### Task 4: Atomic and Idempotent Manual Intake Service

**Files:**
- Create: `backend/src/incident_intelligence/persistence/repositories.py`
- Create: `backend/src/incident_intelligence/persistence/unit_of_work.py`
- Create: `backend/src/incident_intelligence/services/manual_intake.py`
- Create: `backend/tests/integration/services/test_manual_intake.py`

**Interfaces:**
- Consumes: domain records and the six tables from Tasks 2–3
- Produces: `ManualIntakeCommand`
- Produces: `ManualIntakeResult(signal_event_id, alert_id, incident_id, diagnosis_run_id, replayed)`
- Produces: `ManualIntakeService.submit(command, idempotency_key, actor, request_id) -> ManualIntakeResult`
- Produces: `IdempotencyConflict` and `ForbiddenIdentityError` reason-coded exceptions

- [x] **Step 1: Write the failing happy-path integration test**

```python
def test_manual_report_creates_four_records_in_one_transaction(service, session):
    result = service.submit(command, "key-1", "operator-a", "req-1")
    assert result.replayed is False
    assert session.get(SignalEventRow, result.signal_event_id).source == "manual"
    assert session.get(AlertRow, result.alert_id).state == "ACTIVE"
    assert session.get(IncidentRow, result.incident_id).state == "DETECTED"
    assert session.get(DiagnosisRunRow, result.diagnosis_run_id).state == "QUEUED"
```

- [x] **Step 2: Run it and observe the missing service failure**

Run: `cd backend && python -m pytest tests/integration/services/test_manual_intake.py -q`

Expected: FAIL because `ManualIntakeService` does not exist.

- [x] **Step 3: Implement one-transaction creation**

The service performs, in order:

1. recursively reject forbidden identity;
2. canonicalize the accepted command and compute SHA-256;
3. reserve `(scope="manual-report", idempotency_key)`;
4. create immutable SignalEvent with `source_event_id=idempotency_key`;
5. create ACTIVE Alert referencing the signal;
6. create DETECTED Incident referencing the alert;
7. create QUEUED DiagnosisRun referencing incident context version 1;
8. append four audit actions;
9. commit once and return identifiers.

Any failure rolls back every record.

- [x] **Step 4: Write and pass replay and conflict tests**

```python
def test_same_key_and_payload_replays_existing_result(service):
    first = service.submit(command, "key-1", "operator-a", "req-1")
    second = service.submit(command, "key-1", "operator-a", "req-2")
    assert second.model_copy(update={"replayed": False}) == first
    assert second.replayed is True


def test_same_key_with_different_payload_conflicts(service):
    service.submit(command, "key-1", "operator-a", "req-1")
    with pytest.raises(IdempotencyConflict):
        service.submit(changed_command, "key-1", "operator-a", "req-2")
```

Add a concurrent insert test using two sessions. Exactly one transaction creates records; the other re-reads the committed idempotency record and returns the same result.

- [x] **Step 5: Add rollback and audit tests**

Force diagnosis insertion to fail and assert zero signal, alert, incident, diagnosis, ingestion-key, and audit rows remain. On success, assert audit details contain identifiers and reason codes but not the original summary, API token, or request payload.

- [x] **Step 6: Run service integration tests**

Run: `cd backend && python -m pytest tests/integration/services/test_manual_intake.py -q`

Expected: happy path, replay, conflict, concurrency, rollback, forbidden identity, and audit tests all pass.

- [ ] **Step 7: Commit the use case**

```bash
git add backend/src/incident_intelligence/persistence backend/src/incident_intelligence/services backend/tests/integration/services
git commit -m "feat: add idempotent manual intake service"
```

---

### Task 5: Authenticated and Bounded Manual Report API

**Files:**
- Create: `backend/src/incident_intelligence/api/dependencies.py`
- Create: `backend/src/incident_intelligence/api/errors.py`
- Create: `backend/src/incident_intelligence/api/schemas/manual_reports.py`
- Create: `backend/src/incident_intelligence/api/routes/manual_reports.py`
- Create: `backend/tests/api/test_manual_reports.py`
- Modify: `backend/src/incident_intelligence/api/router.py`
- Modify: `backend/src/incident_intelligence/main.py`

**Interfaces:**
- Consumes: `ManualIntakeService.submit` from Task 4
- Produces: `POST /api/v1/manual-reports`
- Produces: bearer-token authentication using `II_API_TOKEN`
- Produces: stable 201, 200 replay, 400, 401, 409, 413, and 422 contracts

- [ ] **Step 1: Write failing authentication and input-bound tests**

```python
def test_manual_report_requires_bearer_token(client, valid_report):
    response = client.post("/api/v1/manual-reports", json=valid_report)
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_unknown_and_experiment_fields_are_rejected(client, auth_headers, valid_report):
    payload = {**valid_report, "scenario_id": "hidden"}
    response = client.post(
        "/api/v1/manual-reports",
        headers={**auth_headers, "Idempotency-Key": "key-1"},
        json=payload,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "forbidden_identity"
```

- [ ] **Step 2: Run the API tests before registering the route**

Run: `cd backend && python -m pytest tests/api/test_manual_reports.py -q`

Expected: FAIL with 404 because the route does not exist.

- [ ] **Step 3: Define the bounded request contract**

The request contains only:

```python
class ManualReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    summary: Annotated[str, StringConstraints(min_length=1, max_length=2_000)]
    severity: Literal["critical", "high", "medium", "low"]
    service: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    environment: Literal["production", "staging", "development", "unknown"]
    observed_at: AwareDatetime
    labels: dict[Annotated[str, StringConstraints(max_length=64)], Annotated[str, StringConstraints(max_length=256)]] = Field(default_factory=dict, max_length=20)
```

Reject timestamps more than five minutes in the future. Do not include arbitrary metadata, attachments, raw payload, query text, or credentials.

- [ ] **Step 4: Implement constant-time bearer authentication and request-size middleware**

Compare the bearer token to `Settings.api_token` with `secrets.compare_digest`. Never log either value. Reject a declared or streamed body exceeding `65536` bytes before JSON parsing with HTTP 413 and code `request_too_large`.

- [ ] **Step 5: Map service outcomes to stable API responses**

First submission returns HTTP 201; an identical replay returns HTTP 200 with the same four IDs and `replayed=true`; changed payload under the same idempotency key returns HTTP 409. Missing or malformed `Idempotency-Key` returns HTTP 400. Validation errors are converted to the common envelope without echoing rejected values.

- [ ] **Step 6: Pass the complete API matrix**

Test: valid create, identical replay, changed-payload conflict, missing/malformed token, missing/oversized idempotency key, unknown field, every forbidden identity spelling at nested label depth, body over 64 KiB, future time, label-count bound, database failure, and absence of secrets/raw bodies in captured logs.

Run: `cd backend && python -m pytest tests/api/test_manual_reports.py -q`

Expected: all manual-report API tests pass.

- [ ] **Step 7: Commit the endpoint**

```bash
git add backend/src/incident_intelligence/api backend/src/incident_intelligence/main.py backend/tests/api/test_manual_reports.py
git commit -m "feat: expose secured manual incident intake"
```

---

### Task 6: Read APIs for the Four Independent Resources

**Files:**
- Create: `backend/src/incident_intelligence/api/schemas/resources.py`
- Create: `backend/src/incident_intelligence/api/routes/resources.py`
- Create: `backend/tests/api/test_resources.py`
- Modify: `backend/src/incident_intelligence/api/router.py`
- Modify: `backend/src/incident_intelligence/persistence/repositories.py`

**Interfaces:**
- Produces: `GET /api/v1/signals/{signal_event_id}`
- Produces: `GET /api/v1/alerts/{alert_id}`
- Produces: `GET /api/v1/incidents/{incident_id}`
- Produces: `GET /api/v1/diagnosis-runs/{diagnosis_run_id}`
- Produces: a stable `resource_not_found` response without revealing whether another token can access a resource

- [ ] **Step 1: Write failing independent-read tests**

```python
def test_created_resources_can_be_read_independently(client, created_ids, auth_headers):
    paths = {
        "signal_event_id": "/api/v1/signals/{}",
        "alert_id": "/api/v1/alerts/{}",
        "incident_id": "/api/v1/incidents/{}",
        "diagnosis_run_id": "/api/v1/diagnosis-runs/{}",
    }
    for field, path in paths.items():
        response = client.get(path.format(created_ids[field]), headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["id"] == created_ids[field]
```

- [ ] **Step 2: Run before route registration**

Run: `cd backend && python -m pytest tests/api/test_resources.py -q`

Expected: FAIL with 404 route-not-found responses.

- [ ] **Step 3: Implement focused repository lookups and response schemas**

Each endpoint reads exactly one resource type. Responses may expose stable identifiers, bounded normalized facts, relationships by ID, current state, timestamps, and version. They must not expose database internals, idempotency keys, request fingerprints, API tokens, raw payloads, or audit details.

- [ ] **Step 4: Add authorization, not-found, malformed-ID, and query-count tests**

All four endpoints require the same bearer token. Malformed prefixed IDs and absent records return 404 with `resource_not_found`. Each endpoint performs at most two SQL statements and does not load unrelated resource bodies.

- [ ] **Step 5: Run the read API suite**

Run: `cd backend && python -m pytest tests/api/test_resources.py -q`

Expected: independent reads, authentication, not-found, field exclusion, and query-count tests pass.

- [ ] **Step 6: Commit the read APIs**

```bash
git add backend/src/incident_intelligence/api backend/src/incident_intelligence/persistence/repositories.py backend/tests/api/test_resources.py
git commit -m "feat: expose incident domain read APIs"
```

---

### Task 7: Phase Verification and Honest Current-State Update

**Files:**
- Modify: `README.md`
- Modify: `docs/current-state.md`
- Modify: `specs/active/multi-source-incident-center.md`
- Create: `docs/verification/2026-08-24-backend-foundation.md`

**Interfaces:**
- Consumes: the complete backend slice from Tasks 1–6
- Produces: reproducible startup and verification instructions
- Produces: evidence mapping for only the acceptance criteria implemented in this phase

- [ ] **Step 1: Run the fresh full verifier**

Run:

```bash
docker compose up -d postgres
cd backend
II_TEST_DATABASE_URL="$II_LOCAL_TEST_DATABASE_URL" ../scripts/verify-backend.sh
```

`II_LOCAL_TEST_DATABASE_URL` is supplied only in the invoking shell or secret manager and is never written to the repository. Expected: Ruff, formatting, Mypy, migration integration tests, API tests, and coverage threshold all pass with zero failures.

- [ ] **Step 2: Perform an actual local API smoke test**

Start the API with environment-supplied local values, submit one manual report, replay it with the same idempotency key, read all four resulting resources, and verify the second response returns the same IDs. Use a temporary local token that is never written to a file or verification report.

- [ ] **Step 3: Verify prohibited content is absent**

Run repository scans for secret-shaped values and prohibited experiment identity. Identity words may appear only in explicit rejection code, boundary tests, and documentation; they must not appear as accepted model or database columns. Record file paths and results, not secret values.

- [ ] **Step 4: Update current state without overstating capability**

Document as implemented only: health/readiness, initial schema, manual intake, idempotent four-record creation, audit actions, and four read endpoints. Keep Alertmanager, CloudEvents, correlation, evidence, Workers, AI, incident transitions, and frontend explicitly marked unimplemented.

- [ ] **Step 5: Record acceptance evidence**

`docs/verification/2026-08-24-backend-foundation.md` records commands, test counts, migration revision, smoke-test outcome, and known gaps. Do not include tokens, complete request payloads, database URLs with credentials, or raw logs.

- [ ] **Step 6: Commit phase evidence and status**

```bash
git add README.md docs/current-state.md specs/active/multi-source-incident-center.md docs/verification/2026-08-24-backend-foundation.md
git commit -m "docs: record backend foundation verification"
```

- [ ] **Step 7: Confirm repository state**

Run: `git status --short && git log --oneline -8`

Expected: no tracked changes remain; the log shows one reviewed commit per task and the final verification commit.

## Plan Self-Review

- **Spec coverage:** This plan covers only the explicitly identified phase-1 subset: engineering baseline, four independent persisted models, separate state families, authorized manual entry, and independent query/audit. All other active-spec requirements are listed as later plans rather than implied as complete.
- **Security boundary:** Authentication, bounded schemas, body limits, recursive prohibited-identity rejection, secret-safe errors/logs, PostgreSQL-only tests, and atomic rollback have explicit tests.
- **Type consistency:** The four ID fields returned by `ManualIntakeResult` are the same IDs consumed by the resource routes and API tests. State values match the architecture document.
- **Failure behavior:** Database readiness, transaction rollback, idempotency conflict, concurrent replay, oversized input, authentication failure, and resource absence each have specified outcomes.
- **No frontend or AI:** No Vue files, AI client, monitoring query, external adapter, fault-injection dependency, or Worker runtime is introduced.
