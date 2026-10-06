from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.clients.central_client import CentralAPIClient
from app.integrations.outlook import GraphClient, OutlookIntegration
from app.integrations.whatsapp import WhatsAppIntegration
from app.schemas.models import InboundMessage, OutboundOutlookRequest, OutboundWhatsAppRequest


class TestRetryBehavior:
    """Tests for retry behavior across all integrations."""

    @pytest.mark.asyncio
    async def test_whatsapp_send_message_retries_on_5xx(self):
        """Test WhatsApp send retries on 5xx errors."""
        integration = WhatsAppIntegration(
            access_token="test-token",
            phone_number_id="123456",
            app_secret="secret",
            verify_token="verify",
        )

        request = OutboundWhatsAppRequest(
            telefone="5511999999999",
            mensagem="Test message",
        )

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=mock_response
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.is_closed = False
        integration._client = mock_client

        with pytest.raises(httpx.HTTPStatusError):
            await integration.send_message(request)

        # Should retry 3 times (default)
        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_whatsapp_send_message_no_retry_on_4xx(self):
        """Test WhatsApp send does not retry on 4xx errors."""
        integration = WhatsAppIntegration(
            access_token="test-token",
            phone_number_id="123456",
            app_secret="secret",
            verify_token="verify",
        )

        request = OutboundWhatsAppRequest(
            telefone="5511999999999",
            mensagem="Test message",
        )

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "400", request=MagicMock(), response=mock_response
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.is_closed = False
        integration._client = mock_client

        with pytest.raises(httpx.HTTPStatusError):
            await integration.send_message(request)

        # Should only be called once (no retry for 4xx)
        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_whatsapp_send_message_retries_on_timeout(self):
        """Test WhatsApp send retries on timeout."""
        integration = WhatsAppIntegration(
            access_token="test-token",
            phone_number_id="123456",
            app_secret="secret",
            verify_token="verify",
        )

        request = OutboundWhatsAppRequest(
            telefone="5511999999999",
            mensagem="Test message",
        )

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("Timeout")
        mock_client.is_closed = False
        integration._client = mock_client

        with pytest.raises(httpx.TimeoutException):
            await integration.send_message(request)

        # Should retry 3 times
        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_graph_get_message_retries_on_5xx(self):
        """Test Graph get_message retries on 5xx."""
        with patch("app.integrations.outlook.msal.ConfidentialClientApplication"):
            client = GraphClient(
                tenant_id="test",
                client_id="test",
                client_secret="test",
                user_id="test@domain.com",
            )

        client._get_access_token = AsyncMock(return_value="token")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=mock_response
        )

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_response
        mock_client.is_closed = False
        client._client = mock_client

        with pytest.raises(httpx.HTTPStatusError):
            await client.get_message("msg123")

        assert mock_client.request.call_count == 3

    @pytest.mark.asyncio
    async def test_graph_get_message_no_retry_on_4xx(self):
        """Test Graph get_message does not retry on 4xx."""
        with patch("app.integrations.outlook.msal.ConfidentialClientApplication"):
            client = GraphClient(
                tenant_id="test",
                client_id="test",
                client_secret="test",
                user_id="test@domain.com",
            )

        client._get_access_token = AsyncMock(return_value="token")

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=mock_response
        )

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_response
        mock_client.is_closed = False
        client._client = mock_client

        with pytest.raises(httpx.HTTPStatusError):
            await client.get_message("msg123")

        assert mock_client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_outlook_send_reply_retries_on_5xx(self):
        """Test Outlook send_reply retries on 5xx."""
        with patch("app.integrations.outlook.GraphClient") as mock_graph_class:
            mock_graph = MagicMock()
            mock_graph_class.return_value = mock_graph
            mock_graph.reply_to_message = AsyncMock()
            mock_graph.reply_to_message.side_effect = httpx.HTTPStatusError(
                "500", request=MagicMock(), response=MagicMock(status_code=500, text="Error")
            )

            integration = OutlookIntegration(
                tenant_id="test",
                client_id="test",
                client_secret="test",
                client_state="test-state",
                subscription_resource="/users/{userId}/messages",
                user_id="test@domain.com",
            )
            integration.graph = mock_graph

            request = OutboundOutlookRequest(
                message_id="msg123",
                resposta="Reply message",
                reply_all=False,
            )

            with pytest.raises(httpx.HTTPStatusError):
                await integration.send_reply(request)

            assert mock_graph.reply_to_message.call_count == 3

    @pytest.mark.asyncio
    async def test_outlook_send_reply_no_retry_on_4xx(self):
        """Test Outlook send_reply does not retry on 4xx."""
        with patch("app.integrations.outlook.GraphClient") as mock_graph_class:
            mock_graph = MagicMock()
            mock_graph_class.return_value = mock_graph
            mock_graph.reply_to_message = AsyncMock()
            mock_graph.reply_to_message.side_effect = httpx.HTTPStatusError(
                "400", request=MagicMock(), response=MagicMock(status_code=400, text="Bad Request")
            )

            integration = OutlookIntegration(
                tenant_id="test",
                client_id="test",
                client_secret="test",
                client_state="test-state",
                subscription_resource="/users/{userId}/messages",
                user_id="test@domain.com",
            )
            integration.graph = mock_graph

            request = OutboundOutlookRequest(
                message_id="msg123",
                resposta="Reply message",
                reply_all=False,
            )

            with pytest.raises(httpx.HTTPStatusError):
                await integration.send_reply(request)

            assert mock_graph.reply_to_message.call_count == 1

    @pytest.mark.asyncio
    async def test_central_client_success_after_retry(self):
        """Test Central API client succeeds after retries."""
        client = CentralAPIClient(base_url="http://test:8000", timeout=5.0)

        message = InboundMessage(
            canal="whatsapp",
            cliente_nome="Test",
            cliente_identificador="5511999999999",
            empresa_dominio="empresa.com",
            mensagem_id_externo="wamid.test",
            pergunta="Test",
        )

        # First two calls fail with 500, third succeeds
        mock_response_fail = MagicMock()
        mock_response_fail.status_code = 500
        mock_response_fail.text = "Internal Server Error"
        mock_response_fail.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=mock_response_fail
        )

        mock_response_success = MagicMock()
        mock_response_success.json.return_value = {"success": True, "data": {"id": "123"}}
        mock_response_success.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.side_effect = [
            mock_response_fail,
            mock_response_fail,
            mock_response_success,
        ]
        mock_client.is_closed = False
        client._client = mock_client

        result = await client.send_inbound_message(message)

        assert result.success is True
        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_central_client_fails_after_max_retries(self):
        """Test Central API client fails after max retries exceeded."""
        client = CentralAPIClient(base_url="http://test:8000", timeout=5.0)

        message = InboundMessage(
            canal="whatsapp",
            cliente_nome="Test",
            cliente_identificador="5511999999999",
            empresa_dominio="empresa.com",
            mensagem_id_externo="wamid.test",
            pergunta="Test",
        )

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=mock_response
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.is_closed = False
        client._client = mock_client

        with pytest.raises(httpx.HTTPStatusError):
            await client.send_inbound_message(message)

        assert mock_client.post.call_count == 3
