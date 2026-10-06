from app.core.config import Settings


def test_settings_defaults():
    """Test default settings values."""
    settings = Settings(
        CENTRAL_API_URL="http://test:8000",
        GATEWAY_API_KEY="test-key",
        WHATSAPP_VERIFY_TOKEN="verify",
        WHATSAPP_APP_SECRET="secret",
        WHATSAPP_ACCESS_TOKEN="token",
        WHATSAPP_PHONE_NUMBER_ID="123456",
        WHATSAPP_BUSINESS_ACCOUNT_ID="789012",
        GRAPH_TENANT_ID="tenant",
        GRAPH_CLIENT_ID="client",
        GRAPH_CLIENT_SECRET="secret",
        GRAPH_NOTIFICATION_CLIENT_STATE="state",
    )

    assert settings.CENTRAL_API_URL == "http://test:8000"
    assert settings.GATEWAY_API_KEY == "test-key"
    assert settings.REQUEST_TIMEOUT == 30.0
    assert settings.RETRY_MAX_ATTEMPTS == 3
    assert settings.IDEMPOTENCY_BACKEND == "database"
    assert settings.LOG_LEVEL == "INFO"


def test_graph_authority_url():
    """Test Graph authority URL construction."""
    settings = Settings(
        CENTRAL_API_URL="http://test:8000",
        GATEWAY_API_KEY="test-key",
        WHATSAPP_VERIFY_TOKEN="verify",
        WHATSAPP_APP_SECRET="secret",
        WHATSAPP_ACCESS_TOKEN="token",
        WHATSAPP_PHONE_NUMBER_ID="123456",
        WHATSAPP_BUSINESS_ACCOUNT_ID="789012",
        GRAPH_TENANT_ID="my-tenant-id",
        GRAPH_CLIENT_ID="client",
        GRAPH_CLIENT_SECRET="secret",
        GRAPH_NOTIFICATION_CLIENT_STATE="state",
    )

    assert settings.graph_authority_url == "https://login.microsoftonline.com/my-tenant-id"


def test_whatsapp_base_url():
    """Test WhatsApp base URL."""
    settings = Settings(
        CENTRAL_API_URL="http://test:8000",
        GATEWAY_API_KEY="test-key",
        WHATSAPP_VERIFY_TOKEN="verify",
        WHATSAPP_APP_SECRET="secret",
        WHATSAPP_ACCESS_TOKEN="token",
        WHATSAPP_PHONE_NUMBER_ID="123456",
        WHATSAPP_BUSINESS_ACCOUNT_ID="789012",
        GRAPH_TENANT_ID="tenant",
        GRAPH_CLIENT_ID="client",
        GRAPH_CLIENT_SECRET="secret",
        GRAPH_NOTIFICATION_CLIENT_STATE="state",
    )

    assert settings.whatsapp_base_url == "https://graph.facebook.com/v20.0"


def test_graph_scopes():
    """Test Graph scopes."""
    settings = Settings(
        CENTRAL_API_URL="http://test:8000",
        GATEWAY_API_KEY="test-key",
        WHATSAPP_VERIFY_TOKEN="verify",
        WHATSAPP_APP_SECRET="secret",
        WHATSAPP_ACCESS_TOKEN="token",
        WHATSAPP_PHONE_NUMBER_ID="123456",
        WHATSAPP_BUSINESS_ACCOUNT_ID="789012",
        GRAPH_TENANT_ID="tenant",
        GRAPH_CLIENT_ID="client",
        GRAPH_CLIENT_SECRET="secret",
        GRAPH_NOTIFICATION_CLIENT_STATE="state",
    )

    assert settings.graph_scopes == ["https://graph.microsoft.com/.default"]
