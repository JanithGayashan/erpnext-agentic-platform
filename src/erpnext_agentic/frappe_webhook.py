import base64
import hashlib
import hmac
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


MAX_WEBHOOK_BODY_BYTES = 16 * 1024


class WebhookSecurityConfigurationError(RuntimeError):
    """Raised when webhook verification is not configured."""


class AutoReorderWebhookEvent(BaseModel):
    """Minimal untrusted event accepted from the ERPNext Webhook."""

    model_config = ConfigDict(extra="forbid")

    doctype: Literal["Material Request"]
    name: str = Field(min_length=1, max_length=140)
    auto_created_via_reorder: bool


def verify_frappe_webhook_signature(
    body: bytes,
    supplied_signature: str | None,
    secret: SecretStr | None,
) -> bool:
    """Verify Frappe's base64-encoded HMAC-SHA256 signature over the raw body."""

    if secret is None or not secret.get_secret_value():
        raise WebhookSecurityConfigurationError(
            "ERPNext webhook verification is not configured"
        )
    if supplied_signature is None:
        return False

    expected_signature = base64.b64encode(
        hmac.new(
            secret.get_secret_value().encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
    ).decode("ascii")
    return hmac.compare_digest(expected_signature, supplied_signature)
