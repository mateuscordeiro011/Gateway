from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.clients.central_client import CentralAPIClient, is_retryable_exception
from app.schemas.models import CentralAPIResponse, InboundMessage


class TestRetryHelpers:
    """Tests for retry helper functions."""

    def test_is_retryable_exception_timeout(self):
        """Test that timeout exceptions are retryable."""
        assert is_retryable_exception(httpx.TimeoutException("timeout")) is True
        assert is_retryable_exception(httpx.ConnectError("connection failed")) is True
        assert is_retryable_exception(httpx.ReadTimeout("read timeout")) is True

    def test_is_retryable_exception_other(self):
        """Test that other exceptions are not retryable."""
        assert is_retryable_exception(ValueError("value error")) is False
        assert is_retryable_exception(RuntimeError("runtime error")) is False

    def test_is_retryable_exception_http_5xx(self):
        """Test that 5xx HTTP errors are retryable."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        exc = httpx.HTTPStatusError("500", request=MagicMock(), response=mock_response)
        assert is_retryable_exception(exc) is True

    def test_is_retryable_exception_http_4xx(self):
        """Test that 4xx HTTP errors are not retryable."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        exc = httpx.HTTPStatusError("400", request=MagicMock(), response=mock_response)
        assert is_retryable_exception(exc) is False

        mock_response.status_code = 401
        exc = httpx.HTTPStatusError("401", request=MagicMock(), response=mock_response)
        assert is_retryable_exception(exc) is False


class TestCentralAPIClient:
    """Tests for CentralAPIClient class."""

    @pytest.fixture
    def central_client(self):
        return CentralAPIClient(base_url="http://test:8000", timeout=5.0)

    @pytest.mark.asyncio
    async def test_send_inbound_message_success(self, central_client):
        """Test successful message sending to Central API."""
        message = InboundMessage(
            canal="whatsapp",
            cliente_nome="Test Client",
            cliente_identificador="5511999999999",
            empresa_dominio="empresa.com",
            mensagem_id_externo="wamid.test123",
            pergunta="Test message",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "message": "Received",
            "data": {"id": "central123"}
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.is_closed = False
        central_client._client = mock_client

        result = await central_client.send_inbound_message(message)

        assert isinstance(result, CentralAPIResponse)
        assert result.success is True
        assert result.data["id"] == "central123"
        mock_client.post.assert_called_once_with(
            "/api/v1/mensagens/receber",
            json=message.model_dump(),
        )

    @pytest.mark.asyncio
    async def test_send_inbound_message_4xx_no_retry(self, central_client):
        """Test that 4xx errors are not retried."""
        message = InboundMessage(
            canal="whatsapp",
            cliente_nome="Test Client",
            cliente_identificador="5511999999999",
            empresa_dominio="empresa.com",
            mensagem_id_externo="wamid.test123",
            pergunta="Test message",
        )

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "400 Bad Request", request=MagicMock(), response=mock_response
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.is_closed = False
        central_client._client = mock_client

        with pytest.raises(httpx.HTTPStatusError):
            await central_client.send_inbound_message(message)

        # Should only be called once (no retry)
        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_send_inbound_message_5xx_retries(self, central_client):
        """Test that 5xx errors trigger retries."""
        message = InboundMessage(
            canal="whatsapp",
            cliente_nome="Test Client",
            cliente_identificador="5511999999999",
            empresa_dominio="empresa.com",
            mensagem_id_externo="wamid.test123",
            pergunta="Test message",
        )

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error", request=MagicMock(), response=mock_response
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.is_closed = False
        central_client._client = mock_client

        with pytest.raises(httpx.HTTPStatusError):
            await central_client.send_inbound_message(message)

        # Should be called 3 times (initial + 2 retries)
        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_send_inbound_message_timeout_retries(self, central_client):
        """Test that timeout errors trigger retries."""
        message = InboundMessage(
            canal="whatsapp",
            cliente_nome="Test Client",
            cliente_identificador="5511999999999",
            empresa_dominio="empresa.com",
            mensagem_id_externo="wamid.test123",
            pergunta="Test message",
        )

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("Timeout")
        mock_client.is_closed = False
        central_client._client = mock_client

        with pytest.raises(httpx.TimeoutException):
            await central_client.send_inbound_message(message)

        # Should be called 3 times (initial + 2 retries)
        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_health_check_success(self, central_client):
        """Test successful health check."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.is_closed = False
        central_client._client = mock_client

        result = await central_client.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self, central_client):
        """Test failed health check."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("Connection failed")
        mock_client.is_closed = False
        central_client._client = mock_client

        result = await central_client.health_check()
        assert result is False


class TestCentralAPIClientRetryBehavior:
    """Test that retry behavior is correctly configured."""

    def test_retry_config_applied(self):
        """Verify the retry decorator is applied to send_inbound_message."""

        client = CentralAPIClient()
        method = client.send_inbound_message

        # Check that the method has retry attributes
        assert hasattr(method, 'retry') or hasattr(method, '__wrapped__')
