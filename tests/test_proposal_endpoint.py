from decimal import Decimal
from datetime import date
from unittest.mock import AsyncMock, patch
from uuid import UUID

from fastapi.testclient import TestClient

from erpnext_agentic.config import Settings
from erpnext_agentic.main import create_app
from erpnext_agentic.proposal_service import ProposalResponse
from erpnext_agentic.replenishment import ReplenishmentProposal


REQUEST_ID = "55aa9796-0939-4f88-984b-55a71af6658a"
VALID_BODY = {
    "request_id": REQUEST_ID,
    "company": "JG Investors (Demo)",
    "item_code": "AGENTIC-TEST-ITEM",
    "warehouse": "Agentic Test Warehouse - JGD",
}


def endpoint_settings(*, platform_api_key: str | None = "test-platform-key") -> Settings:
    return Settings(
        environment="test",
        platform_requester_id="test-requester",
        platform_api_key=platform_api_key,
        erpnext_api_key="fake-erpnext-key",
        erpnext_api_secret="fake-erpnext-secret",
    )


def proposal_response() -> ProposalResponse:
    return ProposalResponse(
        request_id=UUID(REQUEST_ID),
        requester_id="test-requester",
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


def test_proposal_endpoint_requires_platform_authentication() -> None:
    app = create_app(endpoint_settings())

    with TestClient(app) as client:
        response = client.post("/replenishment/proposals", json=VALID_BODY)

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid platform API key"}


def test_proposal_endpoint_fails_when_authentication_is_unconfigured() -> None:
    app = create_app(endpoint_settings(platform_api_key=None))

    with TestClient(app) as client:
        response = client.post("/replenishment/proposals", json=VALID_BODY)

    assert response.status_code == 503


def test_proposal_endpoint_returns_authenticated_dry_run() -> None:
    app = create_app(endpoint_settings())
    mocked_builder = AsyncMock(return_value=proposal_response())

    with patch(
        "erpnext_agentic.main.build_replenishment_proposal",
        mocked_builder,
    ):
        with TestClient(app) as client:
            response = client.post(
                "/replenishment/proposals",
                json=VALID_BODY,
                headers={"X-API-Key": "test-platform-key"},
            )

    assert response.status_code == 200
    assert response.json()["request_id"] == REQUEST_ID
    assert response.json()["requester_id"] == "test-requester"
    assert response.json()["proposal"]["outcome"] == "PROPOSAL"
    assert response.json()["proposal"]["proposed_stock_qty"] == "20"
    mocked_builder.assert_awaited_once()


def test_proposal_endpoint_rejects_body_requester_identity() -> None:
    app = create_app(endpoint_settings())
    body = {**VALID_BODY, "requester_id": "Administrator"}

    with TestClient(app) as client:
        response = client.post(
            "/replenishment/proposals",
            json=body,
            headers={"X-API-Key": "test-platform-key"},
        )

    assert response.status_code == 422


def test_proposal_endpoint_rejects_out_of_scope_request_before_erpnext() -> None:
    app = create_app(endpoint_settings())
    body = {**VALID_BODY, "warehouse": "Other Warehouse"}

    with TestClient(app) as client:
        response = client.post(
            "/replenishment/proposals",
            json=body,
            headers={"X-API-Key": "test-platform-key"},
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Request is outside the configured first-milestone scope"
    }
