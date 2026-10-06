from unittest.mock import patch

import pytest
from fastapi import HTTPException, status

from app.core.security import verify_api_key


class TestSecurity:
    """Tests for security module."""

    @pytest.mark.asyncio
    async def test_verify_api_key_valid(self):
        """Test successful API key verification."""
        with patch("app.core.security.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_API_KEY = "valid-key"

            result = await verify_api_key(x_api_key="valid-key")
            assert result == "valid-key"

    @pytest.mark.asyncio
    async def test_verify_api_key_missing(self):
        """Test missing API key raises 401."""
        with patch("app.core.security.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_API_KEY = "valid-key"

            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(x_api_key=None)

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert "Missing X-API-Key header" in exc_info.value.detail
            assert exc_info.value.headers["WWW-Authenticate"] == "ApiKey"

    @pytest.mark.asyncio
    async def test_verify_api_key_invalid(self):
        """Test invalid API key raises 401."""
        with patch("app.core.security.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_API_KEY = "valid-key"

            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(x_api_key="invalid-key")

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert "Invalid API key" in exc_info.value.detail
            assert exc_info.value.headers["WWW-Authenticate"] == "ApiKey"

    @pytest.mark.asyncio
    async def test_verify_api_key_not_configured(self):
        """Test missing server-side API key raises 500."""
        with patch("app.core.security.get_settings") as mock_settings:
            mock_settings.return_value.GATEWAY_API_KEY = ""

            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(x_api_key="any-key")

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "API key not configured on server" in exc_info.value.detail
