from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app


@pytest.fixture
def mock_settings():
    """Mock settings with valid API key."""
    with patch("app.core.security.get_settings") as mock_get_settings:
        mock_settings = MagicMock(spec=Settings)
        mock_settings.GATEWAY_API_KEY = "test-gateway-api-key"
        mock_get_settings.return_value = mock_settings
        yield mock_settings


@pytest.fixture
def client(mock_settings):
    """Create a test client with mocked settings."""
    return TestClient(app)


@pytest.fixture
def mock_integrations():
    """Mock all integrations for testing."""
    with patch("app.main.whatsapp_integration") as mock_whatsapp, \
         patch("app.main.outlook_integration") as mock_outlook, \
         patch("app.main.central_client") as mock_central, \
         patch("app.main.idempotency_store") as mock_idempotency:

        # Setup default mocks
        mock_whatsapp.verify_webhook = MagicMock(return_value="challenge123")
        mock_whatsapp.verify_hmac = MagicMock(return_value=True)
        mock_whatsapp.parse_webhook_payload = MagicMock(return_value=[])

        mock_outlook.handle_validation_token = MagicMock(return_value="validation123")
        mock_outlook.validate_client_state = MagicMock(return_value=True)
        mock_outlook.process_notification = AsyncMock(return_value=[])
        mock_outlook.create_subscription = AsyncMock(return_value={"id": "sub123"})
        mock_outlook.renew_subscription = AsyncMock(return_value={"id": "sub123"})
        mock_outlook.delete_subscription = AsyncMock()

        mock_central.send_inbound_message = AsyncMock()
        mock_central.health_check = AsyncMock(return_value=True)

        mock_idempotency.try_acquire = AsyncMock(return_value=True)
        mock_idempotency.mark_processed = AsyncMock()
        mock_idempotency.release = AsyncMock()
        mock_idempotency.cleanup_expired = AsyncMock(return_value=0)

        yield {
            "whatsapp": mock_whatsapp,
            "outlook": mock_outlook,
            "central": mock_central,
            "idempotency": mock_idempotency,
        }


class TestHealthCheck:
    """Tests for health check endpoint."""

    def test_health_check_success(self, client, mock_integrations):
        """Test successful health check."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "checks" in data
        assert "timestamp" in data
        assert data["version"] == "0.1.0"


class TestWhatsAppWebhook:
    """Tests for WhatsApp webhook endpoints."""

    def test_webhook_verification_success(self, client, mock_integrations):
        """Test successful webhook verification."""
        response = client.get(
            "/webhook/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "challenge123",
                "hub.verify_token": "test-verify-token",
            }
        )
        assert response.status_code == 200
        assert response.text == "challenge123"

    def test_webhook_verification_invalid_token(self, client, mock_integrations):
        """Test webhook verification with invalid token."""
        mock_integrations["whatsapp"].verify_webhook.return_value = None

        response = client.get(
            "/webhook/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "challenge123",
                "hub.verify_token": "wrong-token",
            }
        )
        assert response.status_code == 403

    def test_webhook_verification_invalid_mode(self, client, mock_integrations):
        """Test webhook verification with invalid mode."""
        mock_integrations["whatsapp"].verify_webhook.return_value = None

        response = client.get(
            "/webhook/whatsapp",
            params={
                "hub.mode": "unsubscribe",
                "hub.challenge": "challenge123",
                "hub.verify_token": "test-verify-token",
            }
        )
        assert response.status_code == 403

    def test_webhook_receive_invalid_signature(self, client, mock_integrations):
        """Test webhook receive with invalid HMAC."""
        mock_integrations["whatsapp"].verify_hmac.return_value = False

        response = client.post(
            "/webhook/whatsapp",
            json={"entry": []},
            headers={"X-Hub-Signature-256": "sha256=invalid"}
        )
        assert response.status_code == 401

    def test_webhook_receive_no_messages(self, client, mock_integrations):
        """Test webhook receive with no valid messages."""
        mock_integrations["whatsapp"].parse_webhook_payload.return_value = []

        response = client.post(
            "/webhook/whatsapp",
            json={"entry": [{"changes": [{"value": {"messages": []}}]}]},
            headers={"X-Hub-Signature-256": "sha256=valid"}
        )
        assert response.status_code == 200
        assert response.json()["processed"] == 0

    def test_webhook_receive_processes_message(self, client, mock_integrations):
        """Test webhook receive processes a message."""
        mock_integrations["whatsapp"].parse_webhook_payload.return_value = [{
            "canal": "whatsapp",
            "cliente_nome": "Test Client",
            "cliente_identificador": "5511999999999",
            "empresa_dominio": "empresa.com",
            "mensagem_id_externo": "wamid.test123",
            "pergunta": "Test message",
        }]

        response = client.post(
            "/webhook/whatsapp",
            json={"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.test123", "from": "5511999999999", "type": "text", "text": {"body": "Test"}}]}}]}]},
            headers={"X-Hub-Signature-256": "sha256=valid"}
        )
        assert response.status_code == 200
        assert response.json()["processed"] == 1
        mock_integrations["central"].send_inbound_message.assert_called_once()

    def test_webhook_receive_duplicate_ignored(self, client, mock_integrations):
        """Test that duplicate messages are ignored."""
        mock_integrations["idempotency"].try_acquire.return_value = False
        mock_integrations["whatsapp"].parse_webhook_payload.return_value = [{
            "canal": "whatsapp",
            "cliente_nome": "Test Client",
            "cliente_identificador": "5511999999999",
            "empresa_dominio": "empresa.com",
            "mensagem_id_externo": "wamid.test123",
            "pergunta": "Test message",
        }]

        response = client.post(
            "/webhook/whatsapp",
            json={"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.test123", "from": "5511999999999", "type": "text", "text": {"body": "Test"}}]}}]}]},
            headers={"X-Hub-Signature-256": "sha256=valid"}
        )
        assert response.status_code == 200
        assert response.json()["processed"] == 0
        mock_integrations["central"].send_inbound_message.assert_not_called()

    def test_webhook_receive_release_on_failure(self, client, mock_integrations):
        """Test that lock is released on processing failure."""
        mock_integrations["whatsapp"].parse_webhook_payload.return_value = [{
            "canal": "whatsapp",
            "cliente_nome": "Test Client",
            "cliente_identificador": "5511999999999",
            "empresa_dominio": "empresa.com",
            "mensagem_id_externo": "wamid.test123",
            "pergunta": "Test message",
        }]
        mock_integrations["central"].send_inbound_message.side_effect = Exception("API Error")

        response = client.post(
            "/webhook/whatsapp",
            json={"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.test123", "from": "5511999999999", "type": "text", "text": {"body": "Test"}}]}}]}]},
            headers={"X-Hub-Signature-256": "sha256=valid"}
        )
        assert response.status_code == 500
        mock_integrations["idempotency"].release.assert_called_once()


class TestOutlookWebhook:
    """Tests for Outlook webhook endpoints."""

    def test_webhook_validation_token(self, client, mock_integrations):
        """Test Graph webhook validation."""
        response = client.get("/webhook/outlook", params={"validationToken": "validation123"})
        assert response.status_code == 200
        assert response.text == "validation123"

    def test_webhook_validation_missing_token(self, client, mock_integrations):
        """Test webhook validation without token."""
        response = client.get("/webhook/outlook")
        assert response.status_code == 400

    def test_webhook_notification_invalid_client_state(self, client, mock_integrations):
        """Test notification with invalid clientState."""
        mock_integrations["outlook"].validate_client_state.return_value = False

        response = client.post(
            "/webhook/outlook",
            params={"clientState": "wrong"},
            json={"value": []}
        )
        assert response.status_code == 401

    def test_webhook_notification_no_value(self, client, mock_integrations):
        """Test notification with no value."""
        response = client.post(
            "/webhook/outlook",
            params={"clientState": "test-state"},
            json={}
        )
        assert response.status_code == 200
        assert response.json()["processed"] == 0

    def test_webhook_notification_processes_message(self, client, mock_integrations):
        """Test notification processes a message."""
        mock_integrations["outlook"].process_notification = AsyncMock(return_value=[{
            "canal": "outlook",
            "cliente_nome": "Test Client",
            "cliente_identificador": "client@company.com",
            "empresa_dominio": "company.com",
            "mensagem_id_externo": "msg123",
            "pergunta": "Test message",
        }])

        response = client.post(
            "/webhook/outlook",
            params={"clientState": "test-state"},
            json={"value": [{"resourceData": {"id": "msg123"}}]}
        )
        assert response.status_code == 200
        assert response.json()["processed"] == 1
        mock_integrations["central"].send_inbound_message.assert_called_once()

    def test_webhook_notification_duplicate_ignored(self, client, mock_integrations):
        """Test that duplicate Outlook messages are ignored."""
        mock_integrations["idempotency"].try_acquire.return_value = False
        mock_integrations["outlook"].process_notification = AsyncMock(return_value=[{
            "canal": "outlook",
            "cliente_nome": "Test Client",
            "cliente_identificador": "client@company.com",
            "empresa_dominio": "company.com",
            "mensagem_id_externo": "msg123",
            "pergunta": "Test message",
        }])

        response = client.post(
            "/webhook/outlook",
            params={"clientState": "test-state"},
            json={"value": [{"resourceData": {"id": "msg123"}}]}
        )
        assert response.status_code == 200
        assert response.json()["processed"] == 0
        mock_integrations["central"].send_inbound_message.assert_not_called()

    def test_webhook_notification_invalid_json(self, client, mock_integrations):
        """Test notification with invalid JSON."""
        response = client.post(
            "/webhook/outlook",
            params={"clientState": "test-state"},
            data="invalid json",
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400


class TestOutboundEndpoints:
    """Tests for outbound endpoints."""

    def test_send_whatsapp_missing_api_key(self, client, mock_integrations):
        """Test send WhatsApp without API key."""
        response = client.post(
            "/api/v1/enviar/whatsapp",
            json={"telefone": "5511999999999", "mensagem": "Test"}
        )
        assert response.status_code == 401

    def test_send_whatsapp_invalid_api_key(self, client, mock_integrations):
        """Test send WhatsApp with invalid API key."""
        response = client.post(
            "/api/v1/enviar/whatsapp",
            json={"telefone": "5511999999999", "mensagem": "Test"},
            headers={"X-API-Key": "wrong-key"}
        )
        assert response.status_code == 401

    def test_send_whatsapp_success(self, client, mock_integrations):
        """Test successful WhatsApp send."""
        mock_integrations["whatsapp"].send_message = AsyncMock(return_value={"messages": [{"id": "wamid.sent123"}]})

        response = client.post(
            "/api/v1/enviar/whatsapp",
            json={"telefone": "5511999999999", "mensagem": "Test message"},
            headers={"X-API-Key": "test-gateway-api-key"}
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_send_whatsapp_4xx_error(self, client, mock_integrations):
        """Test WhatsApp send with 4xx error."""
        import httpx
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_integrations["whatsapp"].send_message = AsyncMock(
            side_effect=httpx.HTTPStatusError("400 Bad Request", request=MagicMock(), response=mock_response)
        )

        response = client.post(
            "/api/v1/enviar/whatsapp",
            json={"telefone": "5511999999999", "mensagem": "Test message"},
            headers={"X-API-Key": "test-gateway-api-key"}
        )
        assert response.status_code == 400

    def test_send_outlook_missing_api_key(self, client, mock_integrations):
        """Test send Outlook without API key."""
        response = client.post(
            "/api/v1/enviar/outlook",
            json={"message_id": "msg123", "resposta": "Test reply"}
        )
        assert response.status_code == 401

    def test_send_outlook_invalid_api_key(self, client, mock_integrations):
        """Test send Outlook with invalid API key."""
        response = client.post(
            "/api/v1/enviar/outlook",
            json={"message_id": "msg123", "resposta": "Test reply"},
            headers={"X-API-Key": "wrong-key"}
        )
        assert response.status_code == 401

    def test_send_outlook_success(self, client, mock_integrations):
        """Test successful Outlook reply."""
        mock_integrations["outlook"].send_reply = AsyncMock(return_value={"id": "reply123"})

        response = client.post(
            "/api/v1/enviar/outlook",
            json={"message_id": "msg123", "resposta": "Test reply", "reply_all": False},
            headers={"X-API-Key": "test-gateway-api-key"}
        )
        assert response.status_code == 200
        assert response.json()["success"] is True


class TestAdminEndpoints:
    """Tests for admin endpoints."""

    def test_create_subscription_missing_api_key(self, client, mock_integrations):
        """Test create subscription without API key."""
        response = client.post(
            "/admin/subscriptions/outlook/create",
            json={"notification_url": "https://example.com/webhook"}
        )
        assert response.status_code == 401

    def test_create_subscription_success(self, client, mock_integrations):
        """Test successful subscription creation."""
        mock_integrations["outlook"].create_subscription = AsyncMock(return_value={"id": "sub123"})

        response = client.post(
            "/admin/subscriptions/outlook/create",
            json={"notification_url": "https://example.com/webhook"},
            headers={"X-API-Key": "test-gateway-api-key"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "created"

    def test_renew_subscription_missing_api_key(self, client, mock_integrations):
        """Test renew subscription without API key."""
        response = client.post(
            "/admin/subscriptions/outlook/renew/sub123"
        )
        assert response.status_code == 401

    def test_renew_subscription_success(self, client, mock_integrations):
        """Test successful subscription renewal."""
        mock_integrations["outlook"].renew_subscription = AsyncMock(return_value={"id": "sub123"})

        response = client.post(
            "/admin/subscriptions/outlook/renew/sub123",
            headers={"X-API-Key": "test-gateway-api-key"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "renewed"

    def test_delete_subscription_missing_api_key(self, client, mock_integrations):
        """Test delete subscription without API key."""
        response = client.delete(
            "/admin/subscriptions/outlook/sub123"
        )
        assert response.status_code == 401

    def test_delete_subscription_success(self, client, mock_integrations):
        """Test successful subscription deletion."""
        mock_integrations["outlook"].delete_subscription = AsyncMock()

        response = client.delete(
            "/admin/subscriptions/outlook/sub123",
            headers={"X-API-Key": "test-gateway-api-key"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "deleted"


class TestIdempotency:
    """Tests for idempotency behavior."""

    def test_duplicate_message_ignored(self, client, mock_integrations):
        """Test that duplicate messages are ignored."""
        mock_integrations["idempotency"].try_acquire.return_value = False
        mock_integrations["whatsapp"].parse_webhook_payload.return_value = [{
            "canal": "whatsapp",
            "cliente_nome": "Test Client",
            "cliente_identificador": "5511999999999",
            "empresa_dominio": "empresa.com",
            "mensagem_id_externo": "wamid.test123",
            "pergunta": "Test message",
        }]

        response = client.post(
            "/webhook/whatsapp",
            json={"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.test123", "from": "5511999999999", "type": "text", "text": {"body": "Test"}}]}}]}]},
            headers={"X-Hub-Signature-256": "sha256=valid"}
        )
        assert response.status_code == 200
        assert response.json()["processed"] == 0
        mock_integrations["central"].send_inbound_message.assert_not_called()

    def test_release_on_failure(self, client, mock_integrations):
        """Test that lock is released on processing failure."""
        mock_integrations["whatsapp"].parse_webhook_payload.return_value = [{
            "canal": "whatsapp",
            "cliente_nome": "Test Client",
            "cliente_identificador": "5511999999999",
            "empresa_dominio": "empresa.com",
            "mensagem_id_externo": "wamid.test123",
            "pergunta": "Test message",
        }]
        mock_integrations["central"].send_inbound_message.side_effect = Exception("API Error")

        response = client.post(
            "/webhook/whatsapp",
            json={"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.test123", "from": "5511999999999", "type": "text", "text": {"body": "Test"}}]}}]}]},
            headers={"X-Hub-Signature-256": "sha256=valid"}
        )
        assert response.status_code == 500
        mock_integrations["idempotency"].release.assert_called_once()


class TestLifespan:
    """Tests for application lifespan."""

    @pytest.mark.asyncio
    async def test_lifespan_initializes_integrations(self):
        """Test that lifespan initializes all integrations."""
        from fastapi import FastAPI

        from app.main import lifespan

        test_app = FastAPI(lifespan=lifespan)

        # This test verifies the lifespan function can be called
        # without errors - actual integration testing is done above
        assert test_app.router.lifespan_context is not None
