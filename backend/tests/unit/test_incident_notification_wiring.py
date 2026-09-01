from pydantic import SecretStr

from incident_intelligence.main import create_app
from incident_intelligence.settings import Settings


def test_app_wires_outbound_feishu_notification_worker() -> None:
    app = create_app(
        Settings(
            database_url="mysql+pymysql://test-client@127.0.0.1/unused",
            api_token=SecretStr("api-token-value"),
            alertmanager_token=SecretStr("alertmanager-token-value"),
            cloudevents_token=SecretStr("cloudevents-token-value"),
            feishu_app_id=SecretStr("cli_test_app"),
            feishu_app_secret=SecretStr("app-secret-value"),
            feishu_verification_token=SecretStr("verification-token-value"),
        )
    )

    assert app.state.incident_notification_service is not None
    assert app.state.incident_notification_runner is not None
