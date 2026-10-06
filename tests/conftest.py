import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest

from app.clients.central_client import CentralAPIClient
from app.core.idempotency import IdempotencyStore, InMemoryIdempotencyStore
from app.integrations.whatsapp import WhatsAppIntegration


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def in_memory_idempotency_store() -> InMemoryIdempotencyStore:
    """Provide an in-memory idempotency store for testing."""
    return InMemoryIdempotencyStore()


@pytest.fixture
async def idempotency_store() -> AsyncGenerator[IdempotencyStore, None]:
    """Provide an idempotency store instance."""
    store = InMemoryIdempotencyStore()
    yield store


@pytest.fixture
def whatsapp_integration(in_memory_idempotency_store):
    """Provide a WhatsAppIntegration instance for testing."""
    return WhatsAppIntegration(
        access_token="test-token",
        phone_number_id="123456789",
        app_secret="test-app-secret",
        verify_token="test-verify-token",
        company_mapping={"5511999999999": "empresa1.com"},
        idempotency_store=in_memory_idempotency_store,
    )


@pytest.fixture
def central_client():
    """Provide a CentralAPIClient instance for testing."""
    return CentralAPIClient(base_url="http://test:8000", timeout=5.0)


@pytest.fixture
def mock_httpx_client():
    """Provide a mock httpx AsyncClient."""
    client = AsyncMock()
    client.is_closed = False
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def sample_inbound_message():
    """Provide a sample inbound message."""
    from app.schemas.models import InboundMessage
    return InboundMessage(
        canal="whatsapp",
        cliente_nome="Test Client",
        cliente_identificador="5511999999999",
        empresa_dominio="empresa.com",
        mensagem_id_externo="wamid.test123",
        pergunta="Test message",
    )


@pytest.fixture
def sample_outbound_whatsapp():
    """Provide a sample outbound WhatsApp request."""
    from app.schemas.models import OutboundWhatsAppRequest
    return OutboundWhatsAppRequest(
        telefone="5511999999999",
        mensagem="Test response",
    )


@pytest.fixture
def sample_outbound_outlook():
    """Provide a sample outbound Outlook request."""
    from app.schemas.models import OutboundOutlookRequest
    return OutboundOutlookRequest(
        message_id="msg123",
        resposta="Test reply",
        reply_all=False,
    )
