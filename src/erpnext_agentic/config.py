from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated application configuration loaded from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="APP_",
        extra="ignore",
    )

    name: str = "ERPNext Agentic Platform"
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    platform_requester_id: str = "local-developer"
    platform_api_key: SecretStr | None = None
    erpnext_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8000")
    erpnext_site_host: str = "development.localhost"
    erpnext_expected_user: str = "agentic-api@jg-investors.test"
    erpnext_company: str = "JG Investors (Demo)"
    erpnext_warehouse: str = "Agentic Test Warehouse - JGD"
    erpnext_item: str = "AGENTIC-TEST-ITEM"
    erpnext_api_key: SecretStr | None = None
    erpnext_api_secret: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    """Create the settings object once per application process."""

    return Settings()
