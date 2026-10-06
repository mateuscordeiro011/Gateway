from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.outlook import EmailCleaner, GraphClient, OutlookIntegration
from app.schemas.models import OutboundOutlookRequest


class TestEmailCleaner:
    """Tests for EmailCleaner class."""

    def test_clean_html_basic(self):
        """Test basic HTML cleaning."""
        html = "<html><body><p>Hello World</p></body></html>"
        result = EmailCleaner.clean_html(html)
        assert "Hello World" in result

    def test_clean_html_removes_scripts(self):
        """Test that script tags are removed."""
        html = "<html><body><script>alert('xss')</script><p>Safe content</p></body></html>"
        result = EmailCleaner.clean_html(html)
        assert "alert" not in result
        assert "Safe content" in result

    def test_clean_html_removes_styles(self):
        """Test that style tags are removed."""
        html = "<html><body><style>body {color: red;}</style><p>Content</p></body></html>"
        result = EmailCleaner.clean_html(html)
        assert "color: red" not in result
        assert "Content" in result

    def test_remove_signatures_standard(self):
        """Test removal of standard signature delimiter."""
        text = "Hello\n\n-- \nJohn Doe\nCompany"
        result = EmailCleaner.remove_signatures(text)
        assert "Hello" in result
        assert "John Doe" not in result

    def test_remove_signatures_mobile(self):
        """Test removal of mobile signatures."""
        text = "Hello\n\nSent from my iPhone"
        result = EmailCleaner.remove_signatures(text)
        assert "Hello" in result
        assert "Sent from my iPhone" not in result

    def test_remove_signatures_portuguese(self):
        """Test removal of Portuguese mobile signatures."""
        text = "Olá\n\nEnviado do meu Android"
        result = EmailCleaner.remove_signatures(text)
        assert "Olá" in result
        assert "Enviado do meu Android" not in result

    def test_remove_quotes_on_wrote(self):
        """Test removal of 'On ... wrote:' quotes."""
        text = "Hello\n\nOn Jan 1, 2024, John wrote:\n> Previous message"
        result = EmailCleaner.remove_quotes(text)
        assert "Hello" in result
        assert "On Jan 1" not in result
        assert "Previous message" not in result

    def test_remove_quotes_portuguese(self):
        """Test removal of Portuguese quotes."""
        text = "Olá\n\nEm 01/01/2024, João escreveu:\n> Mensagem anterior"
        result = EmailCleaner.remove_quotes(text)
        assert "Olá" in result
        assert "Em 01/01" not in result

    def test_remove_quotes_greater_than(self):
        """Test removal of > quoted lines."""
        text = "Hello\n\n> Quoted line\n> Another quoted\nNormal line"
        result = EmailCleaner.remove_quotes(text)
        assert "Hello" in result
        assert "Quoted line" not in result
        assert "Normal line" in result

    def test_clean_full_pipeline(self):
        """Test full cleaning pipeline."""
        html = """
        <html>
            <body>
                <p>Hello, I need help with my order.</p>
                <p>Thanks,</p>
                <p>John</p>
                <p>-- </p>
                <p>Sent from my iPhone</p>
            </body>
        </html>
        """
        result = EmailCleaner.clean(html, None)
        assert "Hello, I need help with my order" in result
        assert "Sent from my iPhone" not in result

    def test_clean_prefers_plain_text(self):
        """Test that plain text is preferred when substantial."""
        html = "<html><body><p>HTML version</p></body></html>"
        plain = "Plain text version with enough content to be preferred"
        result = EmailCleaner.clean(html, plain)
        assert "Plain text version" in result
        assert "HTML version" not in result

    def test_clean_short_plain_text_falls_back_to_html(self):
        """Test fallback to HTML when plain text is too short."""
        html = "<html><body><p>HTML version with more content</p></body></html>"
        plain = "Short"
        result = EmailCleaner.clean(html, plain)
        assert "HTML version" in result


class TestGraphClient:
    """Tests for GraphClient class."""

    @pytest.fixture
    def graph_client(self):
        with patch("app.integrations.outlook.msal.ConfidentialClientApplication"):
            client = GraphClient(
                tenant_id="test-tenant",
                client_id="test-client",
                client_secret="test-secret",
                user_id="test-user@domain.com",
            )
            yield client

    @pytest.mark.asyncio
    async def test_get_access_token_success(self, graph_client):
        """Test successful token acquisition."""
        mock_result = {
            "access_token": "test-token",
            "expires_in": 3600,
        }
        graph_client._msal_app.acquire_token_for_client = MagicMock(return_value=mock_result)

        token = await graph_client._get_access_token()
        assert token == "test-token"

    @pytest.mark.asyncio
    async def test_get_access_token_failure(self, graph_client):
        """Test token acquisition failure."""
        mock_result = {
            "error": "invalid_client",
            "error_description": "Invalid credentials",
        }
        graph_client._msal_app.acquire_token_for_client = MagicMock(return_value=mock_result)

        with pytest.raises(RuntimeError, match="Failed to acquire access token"):
            await graph_client._get_access_token()

    @pytest.mark.asyncio
    async def test_get_message_success(self, graph_client):
        """Test successful message retrieval."""
        graph_client._get_access_token = AsyncMock(return_value="test-token")

        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "msg123", "subject": "Test"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_response
        mock_client.is_closed = False
        graph_client._client = mock_client

        result = await graph_client.get_message("msg123")
        assert result["id"] == "msg123"

    @pytest.mark.asyncio
    async def test_reply_to_message_success(self, graph_client):
        """Test successful reply."""
        graph_client._get_access_token = AsyncMock(return_value="test-token")

        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "reply123"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_response
        mock_client.is_closed = False
        graph_client._client = mock_client

        result = await graph_client.reply_to_message("msg123", "Reply body")
        assert result["id"] == "reply123"


class TestOutlookIntegration:
    """Tests for OutlookIntegration class."""

    @pytest.fixture
    def outlook_integration(self):
        with patch("app.integrations.outlook.GraphClient") as mock_graph:
            mock_graph_instance = MagicMock()
            mock_graph.return_value = mock_graph_instance
            mock_graph_instance.user_id = "test-user@domain.com"

            integration = OutlookIntegration(
                tenant_id="test-tenant",
                client_id="test-client",
                client_secret="test-secret",
                client_state="test-state",
                subscription_resource="/users/{userId}/mailFolders('Inbox')/messages",
                user_id="test-user@domain.com",
            )
            integration.graph = mock_graph_instance
            yield integration

    def test_validate_client_state_valid(self, outlook_integration):
        """Test valid clientState validation."""
        result = outlook_integration.validate_client_state("test-state")
        assert result is True

    def test_validate_client_state_invalid(self, outlook_integration):
        """Test invalid clientState validation."""
        result = outlook_integration.validate_client_state("wrong-state")
        assert result is False

    def test_handle_validation_token(self, outlook_integration):
        """Test validation token handling."""
        token = outlook_integration.handle_validation_token("validation123")
        assert token == "validation123"

    @pytest.mark.asyncio
    async def test_process_notification_skip_own(self, outlook_integration):
        """Test that own messages are skipped after fetching."""
        outlook_integration._is_own_message = MagicMock(return_value=True)
        outlook_integration.graph.get_message = AsyncMock(return_value={
            "id": "msg123",
            "body": {"contentType": "text", "content": "Hello"},
            "from": {"emailAddress": {"address": "service@domain.com"}},
            "sender": {"emailAddress": {"address": "service@domain.com"}},
        })

        notification = {"resourceData": {"id": "msg123"}}
        messages = await outlook_integration.process_notification(notification)

        assert len(messages) == 0
        outlook_integration.graph.get_message.assert_called_once_with("msg123")

    @pytest.mark.asyncio
    async def test_process_notification_external(self, outlook_integration):
        """Test processing external message."""
        outlook_integration._is_own_message = MagicMock(return_value=False)
        outlook_integration.graph.get_message = AsyncMock(return_value={
            "id": "msg123",
            "internetMessageId": "<msg123@domain.com>",
            "conversationId": "conv123",
            "body": {"contentType": "text", "content": "Hello, I need help"},
            "from": {"emailAddress": {"address": "client@company.com", "name": "Client Name"}},
            "sender": {"emailAddress": {"address": "client@company.com"}},
        })
        outlook_integration.email_cleaner.clean = MagicMock(return_value="Hello, I need help")

        notification = {"resourceData": {"id": "msg123"}}
        messages = await outlook_integration.process_notification(notification)

        assert len(messages) == 1
        msg = messages[0]
        assert msg["canal"] == "outlook"
        assert msg["cliente_nome"] == "Client Name"
        assert msg["cliente_identificador"] == "client@company.com"
        assert msg["empresa_dominio"] == "company.com"
        assert msg["mensagem_id_externo"] == "<msg123@domain.com>"
        assert msg["pergunta"] == "Hello, I need help"

    def test_is_own_message_by_sender(self, outlook_integration):
        """Test detection of own message by sender."""
        outlook_integration.graph.user_id = "service@domain.com"
        message = {
            "sender": {"emailAddress": {"address": "service@domain.com"}},
            "from": {"emailAddress": {"address": "other@domain.com"}},
        }
        assert outlook_integration._is_own_message(message) is True

    def test_is_own_message_by_from(self, outlook_integration):
        """Test detection of own message by from."""
        outlook_integration.graph.user_id = "service@domain.com"
        message = {
            "sender": {"emailAddress": {"address": "other@domain.com"}},
            "from": {"emailAddress": {"address": "service@domain.com"}},
        }
        assert outlook_integration._is_own_message(message) is True

    def test_is_own_message_external(self, outlook_integration):
        """Test detection of external message."""
        outlook_integration.graph.user_id = "service@domain.com"
        message = {
            "sender": {"emailAddress": {"address": "client@company.com"}},
            "from": {"emailAddress": {"address": "client@company.com"}},
        }
        assert outlook_integration._is_own_message(message) is False

    @pytest.mark.asyncio
    async def test_send_reply_success(self, outlook_integration):
        """Test successful reply sending."""
        request = OutboundOutlookRequest(
            message_id="msg123",
            resposta="Thank you for your message.",
            reply_all=False,
        )
        outlook_integration.graph.reply_to_message = AsyncMock(return_value={"id": "reply123"})

        result = await outlook_integration.send_reply(request)
        assert result["id"] == "reply123"
        # Check that it was called with the right arguments (using keyword for reply_all)
        outlook_integration.graph.reply_to_message.assert_called_once_with(
            "msg123",
            "Thank you for your message.",
            reply_all=False,
        )
