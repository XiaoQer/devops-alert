from fastapi.testclient import TestClient


def test_create_diagnosis_requires_user_token_and_idempotency_key(client: TestClient) -> None:
    response = client.post(
        f"/api/v1/incidents/inc_{'1' * 32}/diagnosis-runs",
        json={"evidence_run_id": f"evr_{'2' * 32}"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"
