from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from erpnext_agentic.config import Settings
from erpnext_agentic.main import create_app
from erpnext_agentic.proposal_service import ProposalResponse
from erpnext_agentic.replenishment import ReplenishmentProposal
from erpnext_agentic.workflow import ReplenishmentApprovalWorkflow


REQUEST_ID = UUID("55aa9796-0939-4f88-984b-55a71af6658a")
REQUEST_BODY = {
    "request_id": str(REQUEST_ID),
    "company": "JG Investors (Demo)",
    "item_code": "AGENTIC-TEST-ITEM",
    "warehouse": "Agentic Test Warehouse - JGD",
}
REQUESTER_HEADERS = {"X-API-Key": "requester-key"}
APPROVER_HEADERS = {"X-API-Key": "approver-key"}
NOW = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def endpoint_settings(
    *,
    approver_api_key: str | None = "approver-key",
) -> Settings:
    return Settings(
        environment="test",
        platform_requester_id="requester-1",
        platform_api_key="requester-key",
        platform_approver_id="approver-1",
        platform_approver_api_key=approver_api_key,
        erpnext_api_key="fake-erpnext-key",
        erpnext_api_secret="fake-erpnext-secret",
    )


def proposal_response() -> ProposalResponse:
    return ProposalResponse(
        request_id=REQUEST_ID,
        requester_id="requester-1",
        proposal=ReplenishmentProposal(
            outcome="PROPOSAL",
            item_code="AGENTIC-TEST-ITEM",
            warehouse="Agentic Test Warehouse - JGD",
            stock_uom="Nos",
            projected_qty=Decimal("0"),
            reorder_level=Decimal("10"),
            configured_reorder_qty=Decimal("20"),
            deficiency=Decimal("10"),
            proposed_stock_qty=Decimal("20"),
            site_time_zone="Asia/Colombo",
            transaction_date=date(2026, 9, 3),
            schedule_date=date(2026, 9, 6),
            lead_time_days=3,
        ),
    )


def build_test_app(
    *,
    settings: Settings | None = None,
) -> tuple[object, AsyncMock]:
    resolved_settings = settings or endpoint_settings()
    builder = AsyncMock(return_value=proposal_response())
    workflow = ReplenishmentApprovalWorkflow(
        resolved_settings,
        InMemorySaver(),
        proposal_builder=builder,
        clock=lambda: NOW,
    )
    return create_app(resolved_settings, workflow=workflow), builder


def test_workflow_start_requires_requester_authentication() -> None:
    app, builder = build_test_app()

    with TestClient(app) as client:
        response = client.post("/replenishment/workflows", json=REQUEST_BODY)

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid platform API key"}
    builder.assert_not_awaited()


def test_http_workflow_can_start_be_inspected_and_be_approved() -> None:
    app, builder = build_test_app()

    with TestClient(app) as client:
        started = client.post(
            "/replenishment/workflows",
            json=REQUEST_BODY,
            headers=REQUESTER_HEADERS,
        )
        plan_hash = started.json()["plan_hash"]
        inspected = client.get(
            f"/replenishment/workflows/{REQUEST_ID}",
            headers=APPROVER_HEADERS,
        )
        approved = client.post(
            f"/replenishment/workflows/{REQUEST_ID}/decision",
            headers=APPROVER_HEADERS,
            json={
                "plan_hash": plan_hash,
                "decision": "approve",
                "reason": "Reviewed test proposal",
            },
        )

    assert started.status_code == 200
    assert started.json()["status"] == "AWAITING_APPROVAL"
    assert started.json()["waiting_for_approval"] is True
    assert inspected.status_code == 200
    assert inspected.json()["plan_hash"] == plan_hash
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED_NO_WRITE"
    assert approved.json()["waiting_for_approval"] is False
    assert approved.json()["approval"]["approver_id"] == "approver-1"
    assert approved.json()["approval"]["reason"] == "Reviewed test proposal"
    builder.assert_awaited_once()


def test_requester_key_cannot_approve() -> None:
    app, _ = build_test_app()

    with TestClient(app) as client:
        started = client.post(
            "/replenishment/workflows",
            json=REQUEST_BODY,
            headers=REQUESTER_HEADERS,
        )
        response = client.post(
            f"/replenishment/workflows/{REQUEST_ID}/decision",
            headers=REQUESTER_HEADERS,
            json={
                "plan_hash": started.json()["plan_hash"],
                "decision": "approve",
            },
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid platform approver API key"}


def test_mismatched_plan_hash_returns_conflict_and_remains_paused() -> None:
    app, _ = build_test_app()

    with TestClient(app) as client:
        client.post(
            "/replenishment/workflows",
            json=REQUEST_BODY,
            headers=REQUESTER_HEADERS,
        )
        response = client.post(
            f"/replenishment/workflows/{REQUEST_ID}/decision",
            headers=APPROVER_HEADERS,
            json={"plan_hash": "0" * 64, "decision": "approve"},
        )
        unchanged = client.get(
            f"/replenishment/workflows/{REQUEST_ID}",
            headers=REQUESTER_HEADERS,
        )

    assert response.status_code == 409
    assert unchanged.json()["status"] == "AWAITING_APPROVAL"
    assert unchanged.json()["waiting_for_approval"] is True


def test_unknown_workflow_returns_not_found() -> None:
    app, _ = build_test_app()

    with TestClient(app) as client:
        response = client.get(
            "/replenishment/workflows/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            headers=REQUESTER_HEADERS,
        )

    assert response.status_code == 404


def test_decision_fails_safely_when_approver_auth_is_unconfigured() -> None:
    app, _ = build_test_app(settings=endpoint_settings(approver_api_key=None))

    with TestClient(app) as client:
        started = client.post(
            "/replenishment/workflows",
            json=REQUEST_BODY,
            headers=REQUESTER_HEADERS,
        )
        response = client.post(
            f"/replenishment/workflows/{REQUEST_ID}/decision",
            headers=APPROVER_HEADERS,
            json={
                "plan_hash": started.json()["plan_hash"],
                "decision": "approve",
            },
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Platform approver authentication is not configured"
    }


def test_decision_body_cannot_supply_approver_identity_or_timestamp() -> None:
    app, _ = build_test_app()

    with TestClient(app) as client:
        started = client.post(
            "/replenishment/workflows",
            json=REQUEST_BODY,
            headers=REQUESTER_HEADERS,
        )
        response = client.post(
            f"/replenishment/workflows/{REQUEST_ID}/decision",
            headers=APPROVER_HEADERS,
            json={
                "plan_hash": started.json()["plan_hash"],
                "decision": "approve",
                "approver_id": "Administrator",
                "decided_at": "2026-09-03T08:00:00Z",
            },
        )

    assert response.status_code == 422


def test_requester_and_approver_cannot_share_an_api_key() -> None:
    with pytest.raises(
        ValidationError,
        match="requester and approver API keys must be different",
    ):
        endpoint_settings(approver_api_key="requester-key")
