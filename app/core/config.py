import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Central API
    CENTRAL_API_URL: str = Field(default="http://localhost:8000")
    GATEWAY_API_KEY: str = Field(default="")

    # WhatsApp
    WHATSAPP_VERIFY_TOKEN: str = Field(default="")
    WHATSAPP_APP_SECRET: str = Field(default="")
    WHATSAPP_ACCESS_TOKEN: str = Field(default="")
    WHATSAPP_PHONE_NUMBER_ID: str = Field(default="")
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = Field(default="")
    WHATSAPP_COMPANY_MAPPING_FILE: str = Field(default="./config/whatsapp_company_mapping.json")

    # Microsoft Graph
    GRAPH_TENANT_ID: str = Field(default="")
    GRAPH_CLIENT_ID: str = Field(default="")
    GRAPH_CLIENT_SECRET: str = Field(default="")
    GRAPH_NOTIFICATION_CLIENT_STATE: str = Field(default="")
    GRAPH_SUBSCRIPTION_RESOURCE: str = Field(default="/users/{userId}/mailFolders('Inbox')/messages")
    GRAPH_USER_ID: str = Field(default="")

    # Retries
    REQUEST_TIMEOUT: float = Field(default=30.0)
    RETRY_MAX_ATTEMPTS: int = Field(default=3)
    RETRY_MIN_WAIT: float = Field(default=1.0)
    RETRY_MAX_WAIT: float = Field(default=10.0)

    # Idempotency
    IDEMPOTENCY_BACKEND: str = Field(default="database")
    IDEMPOTENCY_TTL_SECONDS: int = Field(default=86400)

    # Redis (optional)
    REDIS_URL: str = Field(default="redis://redis:6379/0")

    # Database
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///./gateway.db")

    # Logs
    LOG_LEVEL: str = Field(default="INFO")

    # Computed properties
    @property
    def whatsapp_company_mapping(self) -> dict[str, str]:
        """Load company mapping from JSON file."""
        path = Path(self.WHATSAPP_COMPANY_MAPPING_FILE)
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @property
    def graph_subscription_resource(self) -> str:
        """Return the subscription resource with userId placeholder resolved."""
        # The userId will be resolved at runtime from the Graph service account
        return self.GRAPH_SUBSCRIPTION_RESOURCE

    @property
    def graph_authority_url(self) -> str:
        """Microsoft identity platform authority URL."""
        return f"https://login.microsoftonline.com/{self.GRAPH_TENANT_ID}"

    @property
    def graph_scopes(self) -> list[str]:
        """Microsoft Graph scopes for Application Permissions."""
        return ["https://graph.microsoft.com/.default"]

    @property
    def graph_base_url(self) -> str:
        """Microsoft Graph API base URL."""
        return "https://graph.microsoft.com/v1.0"

    @property
    def whatsapp_base_url(self) -> str:
        """WhatsApp Cloud API base URL."""
        return "https://graph.facebook.com/v20.0"


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()
