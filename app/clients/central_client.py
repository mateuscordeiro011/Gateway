import logging

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.schemas.models import CentralAPIResponse, InboundMessage

logger = logging.getLogger(__name__)


def is_retryable_exception(exc: Exception) -> bool:
    """Check if exception is retryable (timeout, connection error, or 5xx HTTP error)."""
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadTimeout)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        # Only retry on 5xx errors
        return 500 <= exc.response.status_code < 600
    return False


class CentralAPIClient:
    """HTTP client for communicating with the Central API."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        settings = get_settings()
        self.base_url = (base_url or settings.CENTRAL_API_URL).rstrip("/")
        self.timeout = timeout or settings.REQUEST_TIMEOUT
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout),
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(is_retryable_exception),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def send_inbound_message(self, message: InboundMessage) -> CentralAPIResponse:
        """
        Send normalized inbound message to Central API.

        Args:
            message: Normalized inbound message

        Returns:
            CentralAPIResponse with success status

        Raises:
            httpx.HTTPStatusError: For HTTP errors (4xx not retried, 5xx retried)
            httpx.TimeoutException: For timeout errors (retryable)
            httpx.ConnectError: For connection errors (retryable)
        """
        client = await self._get_client()

        logger.info(
            "Sending inbound message to Central API",
            extra={
                "canal": message.canal,
                "cliente_identificador": message.cliente_identificador,
                "mensagem_id_externo": message.mensagem_id_externo,
            }
        )

        response = await client.post(
            "/api/v1/mensagens/receber",
            json=message.model_dump(),
        )

        # Raise for status - this will raise HTTPStatusError for 4xx and 5xx
        # The retry logic will only retry on 5xx (handled by is_retryable_exception)
        response.raise_for_status()

        data = response.json()
        logger.info(
            "Central API response received",
            extra={"success": data.get("success"), "message_id": data.get("data", {}).get("id")}
        )

        return CentralAPIResponse(**data)

    async def health_check(self) -> bool:
        """Check if Central API is reachable."""
        try:
            client = await self._get_client()
            response = await client.get("/health", timeout=5.0)
            return response.status_code == 200
        except Exception:
            return False
