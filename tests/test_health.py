from fastapi.testclient import TestClient

from erpnext_agentic.config import Settings
from erpnext_agentic.main import create_app


def test_health_reports_service_status() -> None:
    app = create_app(Settings(environment="test"))

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ERPNext Agentic Platform",
        "environment": "test",
    }


def test_erpnext_credentials_are_secret_typed() -> None:
    settings = Settings(
        environment="test",
        erpnext_api_key="test-key",
        erpnext_api_secret="test-secret",
        erpnext_webhook_secret="test-webhook-secret",
    )

    assert settings.erpnext_api_key is not None
    assert settings.erpnext_api_secret is not None
    assert settings.erpnext_webhook_secret is not None
    assert str(settings.erpnext_api_key) == "**********"
    assert str(settings.erpnext_api_secret) == "**********"
    assert str(settings.erpnext_webhook_secret) == "**********"
    assert settings.erpnext_api_key.get_secret_value() == "test-key"
    assert settings.erpnext_api_secret.get_secret_value() == "test-secret"
    assert settings.erpnext_webhook_secret.get_secret_value() == "test-webhook-secret"
