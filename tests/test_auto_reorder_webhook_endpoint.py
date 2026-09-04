import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from erpnext_agentic.config import Settings
from erpnext_agentic.main import create_app
from erpnext_agentic.sourcing_review import (
    AutoReorderReviewResponse,
    AutoReorderSourcingAssessment,
    ItemSupplierReadiness,
    review_id_for_material_request,
)
from erpnext_agentic.sourcing_workflow import AutoReorderSourcingWorkflow


MATERIAL_REQUEST_NAME = "MAT-MR-2026-00003"
REVIEW_ID = review_id_for_material_request(MATERIAL_REQUEST_NAME)
WEBHOOK_SECRET = "private-webhook-secret"
APPROVER_HEADERS = {"X-API-Key": "approver-key"}
REQUESTER_HEADERS = {"X-API-Key": "requester-key"}
NOW = datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc)


def settings(*, webhook_secret: str | None = WEBHOOK_SECRET) -> Settings:
    return Settings(
        environment="test",
        platform_requester_id="requester-1",
        platform_api_key="requester-key",
        platform_approver_id="approver-1",
        platform_approver_api_key="approver-key",
        erpnext_api_key="erpnext-key",
        erpnext_api_secret="erpnext-secret",
        erpnext_webhook_secret=webhook_secret,
    )


def review_response() -> AutoReorderReviewResponse:
    return AutoReorderReviewResponse(
        review_id=REVIEW_ID,
        source_id="erpnext-auto-reorder-webhook",
        assessment=AutoReorderSourcingAssessment(
            material_request_name=MATERIAL_REQUEST_NAME,
            material_request_modified="2026-09-04 09:00:00.000000",
            company="JG Investors (Demo)",
            transaction_date="2026-09-04",
            schedule_date="2026-09-07",
            outcome="SUPPLIER_LINK_REVIEW_REQUIRED",
            explanation="1 of 1 Item rows have no Supplier linked.",
            items=[
                ItemSupplierReadiness(
                    row_name="MR-ITEM-00003",
                    item_code="AGENTIC-TEST-ITEM",
                    warehouse="Agentic Test Warehouse - JGD",
                    requested_stock_qty="20",
                    stock_uom="Nos",
                    linked_suppliers=[],
                    outcome="NO_LINKED_SUPPLIER",
                )
            ],
        ),
    )


def signed_event(
    *,
    auto_created_via_reorder: bool = True,
    secret: str = WEBHOOK_SECRET,
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {
            "doctype": "Material Request",
            "name": MATERIAL_REQUEST_NAME,
            "auto_created_via_reorder": auto_created_via_reorder,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    signature = base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii")
    return body, {
        "Content-Type": "application/json",
        "X-Frappe-Webhook-Signature": signature,
    }


def build_app(
    *,
    configured_settings: Settings | None = None,
) -> tuple[object, AsyncMock]:
    resolved = configured_settings or settings()
    builder = AsyncMock(return_value=review_response())
    workflow = AutoReorderSourcingWorkflow(
        resolved,
        InMemorySaver(),
        review_builder=builder,
        clock=lambda: NOW,
    )
    return create_app(resolved, sourcing_workflow=workflow), builder


def test_signed_erpnext_event_starts_idempotent_review() -> None:
    app, builder = build_app()
    body, headers = signed_event()

    with TestClient(app) as client:
        first = client.post(
            "/erpnext/webhooks/material-request-reorder",
            content=body,
            headers=headers,
        )
        repeated = client.post(
            "/erpnext/webhooks/material-request-reorder",
            content=body,
            headers=headers,
        )

    assert first.status_code == 202
    assert first.json()["review_id"] == str(REVIEW_ID)
    assert first.json()["status"] == "AWAITING_REVIEW"
    assert repeated.json() == first.json()
    builder.assert_awaited_once()


def test_invalid_webhook_signature_is_rejected_before_workflow() -> None:
    app, builder = build_app()
    body, headers = signed_event(secret="wrong-secret")

    with TestClient(app) as client:
        response = client.post(
            "/erpnext/webhooks/material-request-reorder",
            content=body,
            headers=headers,
        )

    assert response.status_code == 401
    builder.assert_not_awaited()


def test_unconfigured_webhook_secret_fails_closed() -> None:
    app, builder = build_app(configured_settings=settings(webhook_secret=None))
    body, headers = signed_event()

    with TestClient(app) as client:
        response = client.post(
            "/erpnext/webhooks/material-request-reorder",
            content=body,
            headers=headers,
        )

    assert response.status_code == 503
    builder.assert_not_awaited()


def test_non_auto_reorder_event_is_rejected() -> None:
    app, builder = build_app()
    body, headers = signed_event(auto_created_via_reorder=False)

    with TestClient(app) as client:
        response = client.post(
            "/erpnext/webhooks/material-request-reorder",
            content=body,
            headers=headers,
        )

    assert response.status_code == 400
    builder.assert_not_awaited()


def test_human_can_inspect_and_approve_event_review() -> None:
    app, builder = build_app()
    body, headers = signed_event()

    with TestClient(app) as client:
        started = client.post(
            "/erpnext/webhooks/material-request-reorder",
            content=body,
            headers=headers,
        ).json()
        inspected = client.get(
            f"/sourcing-reviews/by-material-request/{MATERIAL_REQUEST_NAME}",
            headers=REQUESTER_HEADERS,
        )
        approved = client.post(
            f"/sourcing-reviews/{REVIEW_ID}/decision",
            headers=APPROVER_HEADERS,
            json={
                "assessment_hash": started["assessment_hash"],
                "decision": "approve",
                "reason": "Supplier linkage reviewed",
            },
        )

    assert inspected.status_code == 200
    assert inspected.json()["status"] == "AWAITING_REVIEW"
    assert approved.status_code == 200
    assert approved.json()["status"] == "REVIEWED_NO_WRITE"
    assert builder.await_count == 2
