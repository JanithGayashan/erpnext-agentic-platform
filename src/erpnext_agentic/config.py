from functools import lru_cache
from hmac import compare_digest
from pathlib import Path
from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
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
    platform_requester_id: str = Field(default="local-developer", min_length=1)
    platform_api_key: SecretStr | None = None
    platform_approver_id: str = Field(default="local-approver", min_length=1)
    platform_approver_api_key: SecretStr | None = None
    workflow_checkpoint_path: Path = Path("data/workflows.sqlite3")
    approval_ttl_minutes: int = Field(default=30, ge=1, le=1440)
    allow_self_approval: bool = False
    erpnext_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8000")
    erpnext_site_host: str = "development.localhost"
    erpnext_expected_user: str = "agentic-api@jg-investors.test"
    erpnext_company: str = "JG Investors (Demo)"
    erpnext_warehouse: str = "Agentic Test Warehouse - JGD"
    erpnext_item: str = "AGENTIC-TEST-ITEM"
    erpnext_api_key: SecretStr | None = None
    erpnext_api_secret: SecretStr | None = None

    @model_validator(mode="after")
    def require_distinct_platform_credentials(self) -> Self:
        requester_key = (
            self.platform_api_key.get_secret_value()
            if self.platform_api_key is not None
            else ""
        )
        approver_key = (
            self.platform_approver_api_key.get_secret_value()
            if self.platform_approver_api_key is not None
            else ""
        )
        if requester_key and approver_key and compare_digest(
            requester_key,
            approver_key,
        ):
            raise ValueError("requester and approver API keys must be different")
        if (
            not self.allow_self_approval
            and self.platform_requester_id == self.platform_approver_id
        ):
            raise ValueError(
                "requester and approver identities must be different"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Create the settings object once per application process."""

    return Settings()
