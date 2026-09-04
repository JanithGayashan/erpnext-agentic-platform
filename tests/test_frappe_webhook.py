import base64
import hashlib
import hmac

import pytest
from pydantic import SecretStr

from erpnext_agentic.frappe_webhook import (
    WebhookSecurityConfigurationError,
    verify_frappe_webhook_signature,
)


BODY = b'{"doctype":"Material Request","name":"MAT-MR-2026-00003"}'
SECRET = SecretStr("private-webhook-secret")


def signature(body: bytes = BODY) -> str:
    return base64.b64encode(
        hmac.new(
            SECRET.get_secret_value().encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
    ).decode("ascii")


def test_valid_frappe_signature_is_accepted() -> None:
    assert verify_frappe_webhook_signature(BODY, signature(), SECRET) is True


def test_changed_body_invalidates_frappe_signature() -> None:
    assert (
        verify_frappe_webhook_signature(
            BODY + b" ",
            signature(),
            SECRET,
        )
        is False
    )


def test_missing_signature_is_rejected() -> None:
    assert verify_frappe_webhook_signature(BODY, None, SECRET) is False


def test_missing_webhook_secret_fails_closed() -> None:
    with pytest.raises(WebhookSecurityConfigurationError):
        verify_frappe_webhook_signature(BODY, signature(), None)
