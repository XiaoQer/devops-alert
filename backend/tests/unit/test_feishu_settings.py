from pydantic import SecretStr

from incident_intelligence.settings import Settings


def test_feishu_secrets_are_masked_in_repr_and_json() -> None:
    settings = Settings(
        database_url="mysql+pymysql://tester@127.0.0.1/incident_test",
        api_token=SecretStr("api-token"),
        alertmanager_token=SecretStr("alertmanager-token"),
        cloudevents_token=SecretStr("cloudevents-token"),
        feishu_app_id=SecretStr("cli_test_app"),
        feishu_app_secret=SecretStr("secret-value"),
        feishu_verification_token=SecretStr("verification-value"),
        feishu_encrypt_key=SecretStr("encrypt-value"),
    )

    rendered = f"{settings!r}\n{settings.model_dump_json()}"
    for secret in (
        "cli_test_app",
        "secret-value",
        "verification-value",
        "encrypt-value",
    ):
        assert secret not in rendered


def test_feishu_capability_reports_missing_environment_keys_only() -> None:
    settings = Settings(
        database_url="mysql+pymysql://tester@127.0.0.1/incident_test",
        api_token=SecretStr("api-token"),
        alertmanager_token=SecretStr("alertmanager-token"),
        cloudevents_token=SecretStr("cloudevents-token"),
        feishu_app_id=SecretStr("cli_test_app"),
    )

    capability = settings.feishu_capability()

    assert capability.configured is False
    assert capability.missing_environment_keys == (
        "II_FEISHU_APP_SECRET",
        "II_FEISHU_VERIFICATION_TOKEN",
    )
