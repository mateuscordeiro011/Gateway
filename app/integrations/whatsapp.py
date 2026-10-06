import hashlib
import hmac
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
from app.core.idempotency import IdempotencyStore
from app.schemas.models import OutboundWhatsAppRequest

logger = logging.getLogger(__name__)


def is_retryable_exception(exc: Exception) -> bool:
    """Check if exception is retryable (timeout, connection error, or 5xx HTTP error)."""
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadTimeout)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        # Only retry on 5xx errors
        return 500 <= exc.response.status_code < 600
    return False


class WhatsAppIntegration:
    """WhatsApp Cloud API integration."""

    def __init__(
        self,
        access_token: str | None = None,
        phone_number_id: str | None = None,
        app_secret: str | None = None,
        verify_token: str | None = None,
        company_mapping: dict | None = None,
        idempotency_store: IdempotencyStore | None = None,
    ):
        settings = get_settings()
        self.access_token = access_token or settings.WHATSAPP_ACCESS_TOKEN
        self.phone_number_id = phone_number_id or settings.WHATSAPP_PHONE_NUMBER_ID
        self.app_secret = app_secret or settings.WHATSAPP_APP_SECRET
        self.verify_token = verify_token or settings.WHATSAPP_VERIFY_TOKEN
        self.company_mapping = company_mapping or settings.whatsapp_company_mapping
        self.idempotency_store = idempotency_store
        self.base_url = settings.whatsapp_base_url
        self._client: httpx.AsyncClient | None = None
        self.timeout = settings.REQUEST_TIMEOUT

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout),
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def verify_webhook(self, hub_mode: str, hub_challenge: str, hub_verify_token: str) -> str | None:
        """
        Verify webhook subscription.

        Returns:
            hub_challenge if verification succeeds, None otherwise
        """
        if hub_mode == "subscribe" and hub_verify_token == self.verify_token:
            logger.info("Webhook verification successful")
            return hub_challenge

        logger.warning(
            "Webhook verification failed",
            extra={"hub_mode": hub_mode, "verify_token_match": hub_verify_token == self.verify_token}
        )
        return None

    def verify_hmac(self, payload: bytes, signature_header: str) -> bool:
        """
        Verify X-Hub-Signature-256 HMAC.

        Args:
            payload: Raw request body
            signature_header: X-Hub-Signature-256 header value

        Returns:
            True if valid, False otherwise
        """
        if not signature_header or not signature_header.startswith("sha256="):
            return False

        expected_signature = signature_header[7:]  # Remove "sha256="
        computed_signature = hmac.new(
            self.app_secret.encode("utf-8"),
            payload,
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(computed_signature, expected_signature)

    def extract_company_domain(self, phone_number: str) -> str:
        """Extract company domain from phone number mapping."""
        # Normalize phone number (remove +, spaces, dashes)
        normalized = phone_number.replace("+", "").replace(" ", "").replace("-", "")
        return self.company_mapping.get(normalized, "unknown.com")

    def parse_webhook_payload(self, payload: dict) -> list[dict]:
        """
        Parse WhatsApp webhook payload and extract messages.

        Returns:
            List of normalized message dicts ready for Central API
        """
        messages = []

        try:
            entry = payload.get("entry", [])
            for entry_item in entry:
                changes = entry_item.get("changes", [])
                for change in changes:
                    value = change.get("value", {})
                    if value.get("messages"):
                        for msg in value["messages"]:
                            # Only process text messages for now
                            if msg.get("type") != "text":
                                logger.info(
                                    "Skipping non-text message",
                                    extra={"message_type": msg.get("type"), "message_id": msg.get("id")}
                                )
                                continue

                            # Extract phone number (from field)
                            from_number = msg.get("from", "")
                            if not from_number:
                                logger.warning("Message missing 'from' field", extra={"msg_data": msg})
                                continue

                            # Normalize phone number (E.164 without +)
                            cliente_identificador = from_number.replace("+", "")

                            # Get company domain from mapping
                            empresa_dominio = self.extract_company_domain(from_number)

                            # Build normalized message
                            normalized = {
                                "canal": "whatsapp",
                                "cliente_nome": value.get("contacts", [{}])[0].get("profile", {}).get("name", "Cliente WhatsApp"),
                                "cliente_identificador": cliente_identificador,
                                "empresa_dominio": empresa_dominio,
                                "mensagem_id_externo": msg.get("id", ""),
                                "pergunta": msg.get("text", {}).get("body", ""),
                            }

                            messages.append(normalized)

        except Exception as e:
            logger.error("Error parsing webhook payload", extra={"error": str(e)})
            raise

        return messages

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(is_retryable_exception),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def send_message(self, request: OutboundWhatsAppRequest) -> dict:
        """
        Send a text message via WhatsApp Cloud API.

        Args:
            request: OutboundWhatsAppRequest with telefone and mensagem

        Returns:
            WhatsApp API response

        Raises:
            httpx.HTTPStatusError: For HTTP errors (4xx not retried, 5xx retried)
            httpx.TimeoutException: For timeout errors (retryable)
            httpx.ConnectError: For connection errors (retryable)
        """
        client = await self._get_client()

        payload = {
            "messaging_product": "whatsapp",
            "to": request.telefone,
            "type": "text",
            "text": {"body": request.mensagem},
        }

        logger.info(
            "Sending WhatsApp message",
            extra={"telefone": request.telefone, "message_length": len(request.mensagem)}
        )

        response = await client.post(
            f"/{self.phone_number_id}/messages",
            json=payload,
        )

        # Raise for status - this will raise HTTPStatusError for 4xx and 5xx
        # The retry logic will only retry on 5xx (handled by is_retryable_exception)
        response.raise_for_status()

        data = response.json()
        logger.info(
            "WhatsApp message sent successfully",
            extra={"message_id": data.get("messages", [{}])[0].get("id")}
        )

        return data


async def get_whatsapp_integration(
    idempotency_store: IdempotencyStore | None = None
) -> WhatsAppIntegration:
    """Factory function to create WhatsAppIntegration with dependencies."""
    return WhatsAppIntegration(idempotency_store=idempotency_store)
