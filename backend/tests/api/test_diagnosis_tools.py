from fastapi.testclient import TestClient


def test_diagnosis_snapshot_tool_requires_a_capability_token(client: TestClient) -> None:
    response = client.get(
        f"/api/v1/diagnosis-runs/drun_{'1' * 32}/tools/snapshot",
    )

    assert response.status_code == 401
    assert response.json()["code"] == "diagnosis_capability_required"
