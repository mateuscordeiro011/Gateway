
from fastapi import Header, HTTPException, status

from app.core.config import get_settings


async def verify_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key")
) -> str:
    """
    Validate API key for outbound endpoints.

    Args:
        x_api_key: API key from X-API-Key header

    Returns:
        The validated API key

    Raises:
        HTTPException: 401 if key is missing or invalid
    """
    settings = get_settings()
    expected_key = settings.GATEWAY_API_KEY

    if not expected_key:
        # If no key configured, deny all requests (secure by default)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API key not configured on server"
        )

    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if x_api_key != expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return x_api_key
