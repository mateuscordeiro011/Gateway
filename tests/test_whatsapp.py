import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integrations.whatsapp import WhatsAppIntegration
from app.schemas.models import OutboundWhatsAppRequest


@pytest.fixture
def whatsapp_integration():
    """Create a WhatsAppIntegration instance for testing."""
    return WhatsAppIntegration(
        access_token="test-token",
        phone_number_id="123456789",
        app_secret="test-app-secret",
        verify_token="test-verify-token",
        company_mapping={"5511999999999": "empresa1.com"},
    )


def test_verify_webhook_success(whatsapp_integration):
    """Test successful webhook verification."""
    challenge = whatsapp_integration.verify_webhook(
        hub_mode="subscribe",
        hub_challenge="challenge123",
        hub_verify_token="test-verify-token"
    )
    assert challenge == "challenge123"


def test_verify_webhook_invalid_token(whatsapp_integration):
    """Test webhook verification with invalid token."""
    challenge = whatsapp_integration.verify_webhook(
        hub_mode="subscribe",
        hub_challenge="challenge123",
        hub_verify_token="wrong-token"
    )
    assert challenge is None


def test_verify_webhook_invalid_mode(whatsapp_integration):
    """Test webhook verification with invalid mode."""
    challenge = whatsapp_integration.verify_webhook(
        hub_mode="unsubscribe",
        hub_challenge="challenge123",
        hub_verify_token="test-verify-token"
    )
    assert challenge is None


def test_verify_hmac_valid(whatsapp_integration):
    """Test valid HMAC verification."""
    payload = b'{"test": "data"}'
    # Compute correct signature
    signature = hmac.new(
        b"test-app-secret",
        payload,
        hashlib.sha256
    ).hexdigest()
    signature_header = f"sha256={signature}"

    result = whatsapp_integration.verify_hmac(payload, signature_header)
    assert result is True


def test_verify_hmac_invalid(whatsapp_integration):
    """Test invalid HMAC verification."""
    payload = b'{"test": "data"}'
    signature_header = "sha256=invalidsignature"

    result = whatsapp_integration.verify_hmac(payload, signature_header)
    assert result is False


def test_verify_hmac_missing_header(whatsapp_integration):
    """Test HMAC verification with missing header."""
    payload = b'{"test": "data"}'

    result = whatsapp_integration.verify_hmac(payload, "")
    assert result is False

    result = whatsapp_integration.verify_hmac(payload, None)
    assert result is False


def test_verify_hmac_wrong_algorithm(whatsapp_integration):
    """Test HMAC verification with wrong algorithm prefix."""
    payload = b'{"test": "data"}'
    signature_header = "sha1=invalidsignature"

    result = whatsapp_integration.verify_hmac(payload, signature_header)
    assert result is False


def test_extract_company_domain_mapped(whatsapp_integration):
    """Test company domain extraction for mapped number."""
    domain = whatsapp_integration.extract_company_domain("+55 11 99999-9999")
    assert domain == "empresa1.com"


def test_extract_company_domain_unmapped(whatsapp_integration):
    """Test company domain extraction for unmapped number."""
    domain = whatsapp_integration.extract_company_domain("5511888888888")
    assert domain == "unknown.com"


def test_parse_webhook_payload_text_message(whatsapp_integration):
    """Test parsing webhook payload with text message."""
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "contacts": [{"profile": {"name": "João Silva"}}],
                    "messages": [{
                        "id": "wamid.test123",
                        "from": "5511999999999",
                        "type": "text",
                        "text": {"body": "Olá, preciso de ajuda"}
                    }]
                }
            }]
        }]
    }

    messages = whatsapp_integration.parse_webhook_payload(payload)

    assert len(messages) == 1
    msg = messages[0]
    assert msg["canal"] == "whatsapp"
    assert msg["cliente_nome"] == "João Silva"
    assert msg["cliente_identificador"] == "5511999999999"
    assert msg["empresa_dominio"] == "empresa1.com"
    assert msg["mensagem_id_externo"] == "wamid.test123"
    assert msg["pergunta"] == "Olá, preciso de ajuda"


def test_parse_webhook_payload_non_text_skipped(whatsapp_integration):
    """Test that non-text messages are skipped."""
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "contacts": [{"profile": {"name": "João Silva"}}],
                    "messages": [{
                        "id": "wamid.test123",
                        "from": "5511999999999",
                        "type": "image",
                        "image": {"caption": "Photo"}
                    }]
                }
            }]
        }]
    }

    messages = whatsapp_integration.parse_webhook_payload(payload)
    assert len(messages) == 0


def test_parse_webhook_payload_missing_from(whatsapp_integration):
    """Test parsing payload with missing from field."""
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "contacts": [{"profile": {"name": "João Silva"}}],
                    "messages": [{
                        "id": "wamid.test123",
                        "type": "text",
                        "text": {"body": "Olá"}
                    }]
                }
            }]
        }]
    }

    messages = whatsapp_integration.parse_webhook_payload(payload)
    assert len(messages) == 0


@pytest.mark.asyncio
async def test_send_message_success(whatsapp_integration):
    """Test successful message sending."""
    request = OutboundWhatsAppRequest(
        telefone="5511999999999",
        mensagem="Olá! Como posso ajudar?"
    )

    # Mock the HTTP client
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "messages": [{"id": "wamid.sent123"}]
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.is_closed = False

    whatsapp_integration._client = mock_client

    result = await whatsapp_integration.send_message(request)

    assert result == {"messages": [{"id": "wamid.sent123"}]}
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_send_message_invalid_telefone(whatsapp_integration):
    """Test that OutboundWhatsAppRequest validation rejects invalid phone."""
    # This should fail at model creation time, not at send time
    with pytest.raises(ValueError, match="Telefone deve conter apenas digitos"):
        OutboundWhatsAppRequest(
            telefone="invalid",
            mensagem="Teste"
        )
