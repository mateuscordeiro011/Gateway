import hmac
import logging
import re
from datetime import UTC

import httpx
import msal
from bs4 import BeautifulSoup
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.core.idempotency import IdempotencyStore
from app.schemas.models import OutboundOutlookRequest

logger = logging.getLogger(__name__)


def is_retryable_exception(exc: Exception) -> bool:
    """Check if exception is retryable (timeout, connection error, or 5xx HTTP error)."""
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadTimeout)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        # Only retry on 5xx errors
        return 500 <= exc.response.status_code < 600
    return False


class EmailCleaner:
    """Clean email content by removing HTML, signatures, quotes, etc."""

    # Common signature patterns
    SIGNATURE_PATTERNS = [
        r"(?i)^--\s*$",  # Standard signature delimiter
        r"(?i)^sent from my (iPhone|iPad|Android|mobile device)",
        r"(?i)^enviado do meu (iPhone|iPad|Android|dispositivo m[oó]vel)",
        r"(?i)^get outlook for (ios|android)",
        r"(?i)^obtenha o outlook para (ios|android)",
    ]

    # Quote patterns (reply headers)
    QUOTE_PATTERNS = [
        r"(?i)^on .* wrote:",
        r"(?i)^em .* escreveu:",
        r"(?i)^de: .*",
        r"(?i)^from: .*",
        r"(?i)^sent: .*",
        r"(?i)^enviado: .*",
        r"(?i)^to: .*",
        r"(?i)^para: .*",
        r"(?i)^subject: .*",
        r"(?i)^assunto: .*",
        r"(?i)^cc: .*",
        r"(?i)^c[oó]pia: .*",
    ]

    @classmethod
    def clean_html(cls, html: str) -> str:
        """Convert HTML to plain text and clean up."""
        if not html:
            return ""

        soup = BeautifulSoup(html, "lxml")

        # Remove script and style elements
        for element in soup(["script", "style", "meta", "link", "head", "title"]):
            element.decompose()

        # Get text with reasonable spacing
        text = soup.get_text(separator="\n", strip=True)

        # Normalize whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)

        return text.strip()

    @classmethod
    def remove_signatures(cls, text: str) -> str:
        """Remove common email signatures."""
        lines = text.split("\n")
        cleaned_lines = []
        signature_started = False

        for line in lines:
            stripped = line.strip()

            # Check if this line starts a signature
            if not signature_started:
                for pattern in cls.SIGNATURE_PATTERNS:
                    if re.match(pattern, stripped):
                        signature_started = True
                        break

            if not signature_started:
                cleaned_lines.append(line)

        return "\n".join(cleaned_lines).strip()

    @classmethod
    def remove_quotes(cls, text: str) -> str:
        """Remove quoted/replied content."""
        lines = text.split("\n")
        cleaned_lines = []
        in_quote = False

        for line in lines:
            stripped = line.strip()
            quote_started_this_line = False

            # Check if this line starts a quote block
            if not in_quote:
                for pattern in cls.QUOTE_PATTERNS:
                    if re.match(pattern, stripped):
                        in_quote = True
                        quote_started_this_line = True
                        break

            # Also detect quote blocks starting with >
            if not in_quote and stripped.startswith(">"):
                in_quote = True
                quote_started_this_line = True

            # Exit quote mode if we hit a non-quoted, non-empty line
            # But NOT on the line that just started the quote
            if in_quote and not quote_started_this_line and not stripped.startswith(">") and stripped and not re.match(r"(?i)^(on|em|de|from|sent|enviado|to|para|subject|assunto|cc|c[oó]pia):", stripped):
                in_quote = False

            if not in_quote:
                cleaned_lines.append(line)

        return "\n".join(cleaned_lines).strip()

    @classmethod
    def clean(cls, html: str, plain_text: str | None = None) -> str:
        """
        Clean email content.

        Args:
            html: HTML body of the email
            plain_text: Optional plain text body (preferred if available)

        Returns:
            Cleaned plain text
        """
        # Prefer plain text if available and substantial
        if plain_text and len(plain_text.strip()) > 50:
            text = plain_text
        else:
            text = cls.clean_html(html)

        # Remove signatures
        text = cls.remove_signatures(text)

        # Remove quotes
        text = cls.remove_quotes(text)

        # Final cleanup
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

        return text


class GraphClient:
    """Microsoft Graph API client using MSAL for Application Permissions."""

    def __init__(
        self,
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        user_id: str | None = None,  # The service account mailbox to monitor
    ):
        settings = get_settings()
        self.tenant_id = tenant_id or settings.GRAPH_TENANT_ID
        self.client_id = client_id or settings.GRAPH_CLIENT_ID
        self.client_secret = client_secret or settings.GRAPH_CLIENT_SECRET
        self.user_id = user_id  # Will be resolved from Graph if not provided
        self.authority = settings.graph_authority_url
        self.scopes = settings.graph_scopes
        self.base_url = settings.graph_base_url
        self._client: httpx.AsyncClient | None = None
        self._access_token: str | None = None
        self._token_expires_at: float = 0
        self.timeout = settings.REQUEST_TIMEOUT
        self._msal_app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=self.authority,
            client_credential=self.client_secret,
        )

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

    async def _get_access_token(self) -> str:
        """Get a valid access token using client credentials flow."""
        import time

        # Check if current token is still valid (with 5 min buffer)
        if self._access_token and time.time() < self._token_expires_at - 300:
            return self._access_token

        logger.info("Acquiring new access token from Microsoft Graph")

        result = self._msal_app.acquire_token_for_client(scopes=self.scopes)

        if "access_token" not in result:
            error = result.get("error_description", "Unknown error")
            logger.error("Failed to acquire access token", extra={"error": error})
            raise RuntimeError(f"Failed to acquire access token: {error}")

        self._access_token = result["access_token"]
        # expires_in is in seconds
        self._token_expires_at = time.time() + result.get("expires_in", 3600)

        logger.info("Access token acquired successfully")
        return self._access_token

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Make an authenticated request to Graph API."""
        token = await self._get_access_token()

        client = await self._get_client()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"

        response = await client.request(method, path, headers=headers, **kwargs)
        return response

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(is_retryable_exception),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def get_message(self, message_id: str) -> dict:
        """
        Get a full message by ID.

        Args:
            message_id: Microsoft Graph message ID

        Returns:
            Full message object
        """
        # Expand relevant fields
        params = {
            "$expand": "replyTo,from,sender,toRecipients,ccRecipients,body,internetMessageHeaders"
        }

        response = await self._request("GET", f"/users/{self.user_id}/messages/{message_id}", params=params)
        response.raise_for_status()
        return response.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(is_retryable_exception),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def reply_to_message(self, message_id: str, reply_body: str, reply_all: bool = False) -> dict:
        """
        Reply to a message in the same thread.

        Args:
            message_id: ID of the message to reply to
            reply_body: HTML body of the reply
            reply_all: Whether to reply to all recipients

        Returns:
            Created reply message
        """
        payload = {
            "comment": reply_body,
        }

        if reply_all:
            # replyAll includes all recipients
            endpoint = f"/users/{self.user_id}/messages/{message_id}/replyAll"
        else:
            # reply only to sender
            endpoint = f"/users/{self.user_id}/messages/{message_id}/reply"

        response = await self._request("POST", endpoint, json=payload)
        response.raise_for_status()
        return response.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(is_retryable_exception),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def create_subscription(
        self,
        resource: str,
        notification_url: str,
        client_state: str,
        expiration_minutes: int = 4230,  # Max ~3 days (4320 min), use slightly less
    ) -> dict:
        """
        Create a Microsoft Graph subscription for change notifications.

        Args:
            resource: Resource to subscribe to (e.g., /users/{id}/mailFolders('Inbox')/messages)
            notification_url: Webhook URL to receive notifications
            client_state: Opaque string for validation
            expiration_minutes: Subscription lifetime in minutes

        Returns:
            Subscription object
        """
        from datetime import datetime, timedelta

        expiration = datetime.now(UTC) + timedelta(minutes=expiration_minutes)

        payload = {
            "changeType": "created",
            "notificationUrl": notification_url,
            "resource": resource,
            "expirationDateTime": expiration.isoformat(),
            "clientState": client_state,
            "includeResourceData": False,
        }

        response = await self._request("POST", "/subscriptions", json=payload)
        response.raise_for_status()
        return response.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(is_retryable_exception),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def renew_subscription(self, subscription_id: str, expiration_minutes: int = 4230) -> dict:
        """
        Renew an existing subscription.

        Args:
            subscription_id: ID of the subscription to renew
            expiration_minutes: New lifetime in minutes

        Returns:
            Updated subscription object
        """
        from datetime import datetime, timedelta

        expiration = datetime.now(UTC) + timedelta(minutes=expiration_minutes)

        payload = {
            "expirationDateTime": expiration.isoformat(),
        }

        response = await self._request("PATCH", f"/subscriptions/{subscription_id}", json=payload)
        response.raise_for_status()
        return response.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(is_retryable_exception),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def delete_subscription(self, subscription_id: str) -> None:
        """Delete a subscription."""
        response = await self._request("DELETE", f"/subscriptions/{subscription_id}")
        response.raise_for_status()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(is_retryable_exception),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def get_user(self) -> dict:
        """Get the service account user info."""
        response = await self._request("GET", f"/users/{self.user_id}")
        response.raise_for_status()
        return response.json()

    async def resolve_user_id(self, email: str) -> str:
        """Resolve user ID from email address."""
        if self.user_id:
            return self.user_id

        # Try to get by email
        response = await self._request("GET", f"/users/{email}")
        if response.status_code == 200:
            user = response.json()
            self.user_id = user["id"]
            return self.user_id

        raise ValueError(f"Could not resolve user ID for email: {email}")


class OutlookIntegration:
    """Outlook/Microsoft Graph integration for inbound and outbound."""

    def __init__(
        self,
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        client_state: str | None = None,
        subscription_resource: str | None = None,
        user_id: str | None = None,
        idempotency_store: IdempotencyStore | None = None,
    ):
        settings = get_settings()
        self.tenant_id = tenant_id or settings.GRAPH_TENANT_ID
        self.client_id = client_id or settings.GRAPH_CLIENT_ID
        self.client_secret = client_secret or settings.GRAPH_CLIENT_SECRET
        self.client_state = client_state or settings.GRAPH_NOTIFICATION_CLIENT_STATE
        self.subscription_resource = subscription_resource or settings.GRAPH_SUBSCRIPTION_RESOURCE
        self.user_id = user_id  # Service account email or ID
        self.idempotency_store = idempotency_store

        self.graph = GraphClient(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.client_secret,
            user_id=self.user_id,
        )
        self.email_cleaner = EmailCleaner()

    async def initialize(self) -> None:
        """Initialize the Graph client and resolve user_id if needed."""
        # Resolve user_id from email if not already set
        if not self.graph.user_id and "@" in str(self.user_id or ""):
            # user_id is actually an email
            email = self.user_id
            self.graph.user_id = await self.graph.resolve_user_id(email)
            logger.info(f"Resolved user_id: {self.graph.user_id}")

        # If still no user_id, try to get from config or default
        if not self.graph.user_id:
            # This would need to be configured - for now we'll require it
            pass

    async def close(self) -> None:
        """Close the Graph client."""
        await self.graph.close()

    def validate_client_state(self, client_state: str) -> bool:
        """Validate clientState from webhook notification."""
        return hmac.compare_digest(client_state, self.client_state)

    def handle_validation_token(self, validation_token: str) -> str:
        """Handle initial webhook validation - return the token."""
        logger.info("Webhook validation token received")
        return validation_token

    async def process_notification(self, notification: dict) -> list[dict]:
        """
        Process a Graph change notification.

        Returns:
            List of normalized messages for Central API
        """
        messages = []

        # Get the resource data (message ID)
        resource_data = notification.get("resourceData", {})
        message_id = resource_data.get("id")

        if not message_id:
            logger.warning("Notification missing resource ID", extra={"notification": notification})
            return messages

        # Get full message details
        try:
            message = await self.graph.get_message(message_id)
        except Exception as e:
            logger.error("Failed to fetch message", extra={"message_id": message_id, "error": str(e)})
            raise

        # Skip messages sent by ourselves
        if self._is_own_message(message):
            logger.info("Skipping own message", extra={"message_id": message_id})
            return messages

        # Extract and clean content
        html_body = message.get("body", {}).get("content", "")

        # Body content type
        content_type = message.get("body", {}).get("contentType", "html")
        if content_type == "text":
            plain_text = html_body
            html_body = ""
        else:
            plain_text = None

        cleaned_text = self.email_cleaner.clean(html_body, plain_text)

        # Extract sender info
        from_info = message.get("from", {}).get("emailAddress", {})
        sender_email = from_info.get("address", "")
        sender_name = from_info.get("name", "Cliente Outlook")

        # Extract conversation/thread info
        conversation_id = message.get("conversationId", "")
        internet_message_id = message.get("internetMessageId", message_id)

        # Build normalized message
        normalized = {
            "canal": "outlook",
            "cliente_nome": sender_name,
            "cliente_identificador": sender_email,
            "empresa_dominio": sender_email.split("@")[-1] if "@" in sender_email else "unknown.com",
            "mensagem_id_externo": internet_message_id,
            "pergunta": cleaned_text,
        }

        messages.append(normalized)

        logger.info(
            "Processed Outlook notification",
            extra={
                "message_id": message_id,
                "sender": sender_email,
                "conversation_id": conversation_id,
            }
        )

        return messages

    def _is_own_message(self, message: dict) -> bool:
        """Check if message was sent by the service account."""
        if not self.graph.user_id:
            return False

        sender = message.get("sender", {}).get("emailAddress", {}).get("address", "").lower()
        from_addr = message.get("from", {}).get("emailAddress", {}).get("address", "").lower()

        return sender == self.graph.user_id.lower() or from_addr == self.graph.user_id.lower()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(is_retryable_exception),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def send_reply(self, request: OutboundOutlookRequest) -> dict:
        """
        Send a reply to an Outlook message.

        Args:
            request: OutboundOutlookRequest with message_id and resposta

        Returns:
            Graph API response
        """
        # Convert plain text to simple HTML
        html_body = request.resposta.replace("\n", "<br>")

        return await self.graph.reply_to_message(
            request.message_id,
            html_body,
            reply_all=request.reply_all,
        )

    async def create_subscription(self, notification_url: str) -> dict:
        """Create a Graph subscription for new emails."""
        resource = self.subscription_resource

        # Replace {userId} placeholder
        if "{userId}" in resource:
            if not self.graph.user_id:
                raise ValueError("user_id must be resolved before creating subscription")
            resource = resource.replace("{userId}", self.graph.user_id)

        return await self.graph.create_subscription(
            resource=resource,
            notification_url=notification_url,
            client_state=self.client_state,
        )

    async def renew_subscription(self, subscription_id: str) -> dict:
        """Renew a Graph subscription."""
        return await self.graph.renew_subscription(subscription_id)

    async def delete_subscription(self, subscription_id: str) -> None:
        """Delete a Graph subscription."""
        return await self.graph.delete_subscription(subscription_id)


async def get_outlook_integration(
    idempotency_store: IdempotencyStore | None = None
) -> OutlookIntegration:
    """Factory function to create OutlookIntegration with dependencies."""
    settings = get_settings()

    # The user_id (service account email) should come from config
    # For now, we'll use a placeholder that needs to be configured
    user_id = getattr(settings, "GRAPH_USER_ID", None)

    integration = OutlookIntegration(
        client_state=settings.GRAPH_NOTIFICATION_CLIENT_STATE,
        subscription_resource=settings.GRAPH_SUBSCRIPTION_RESOURCE,
        user_id=user_id,
        idempotency_store=idempotency_store,
    )

    await integration.initialize()
    return integration
