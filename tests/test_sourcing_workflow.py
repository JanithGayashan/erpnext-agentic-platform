from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from erpnext_agentic.config import Settings
from erpnext_agentic.sourcing_review import (
    AutoReorderReviewRequest,
    AutoReorderReviewResponse,
    AutoReorderSourcingAssessment,
    ItemSupplierReadiness,
    SourcingReviewScopeError,
)
from erpnext_agentic.sourcing_workflow import (
    AutoReorderSourcingWorkflow,
    SourcingReviewApprovalMismatchError,
    SourcingReviewStateError,
    open_sqlite_sourcing_workflow,
)


REVIEW_ID = UUID("f2756d51-f49e-5d4f-a63e-30b18d7fb886")
NOW = datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc)


def settings() -> Settings:
    return Settings(
        environment="test",
        platform_requester_id="requester-1",
        platform_api_key="requester-key",
        platform_approver_id="approver-1",
        platform_approver_api_key="approver-key",
        erpnext_api_key="erpnext-key",
        erpnext_api_secret="erpnext-secret",
    )


def request() -> AutoReorderReviewRequest:
    return AutoReorderReviewRequest(
        review_id=REVIEW_ID,
        material_request_name="MAT-MR-2026-00003",
    )


def review_response(
    *,
    suppliers: list[str] | None = None,
    modified: str = "2026-09-04 09:00:00.000000",
) -> AutoReorderReviewResponse:
    suppliers = suppliers or []
    has_supplier = bool(suppliers)
    return AutoReorderReviewResponse(
        review_id=REVIEW_ID,
        source_id="erpnext-auto-reorder-webhook",
        assessment=AutoReorderSourcingAssessment(
            material_request_name="MAT-MR-2026-00003",
            material_request_modified=modified,
            company="JG Investors (Demo)",
            transaction_date="2026-09-04",
            schedule_date="2026-09-07",
            outcome=(
                "READY_FOR_SOURCING_REVIEW"
                if has_supplier
                else "SUPPLIER_LINK_REVIEW_REQUIRED"
            ),
            explanation=(
                "Every Item row has at least one Supplier link."
                if has_supplier
                else "1 of 1 Item rows have no Supplier linked."
            ),
            items=[
                ItemSupplierReadiness(
                    row_name="MR-ITEM-00003",
                    item_code="AGENTIC-TEST-ITEM",
                    warehouse="Agentic Test Warehouse - JGD",
                    requested_stock_qty="20",
                    stock_uom="Nos",
                    linked_suppliers=suppliers,
                    outcome=(
                        "LINKED_SUPPLIER_PRESENT"
                        if has_supplier
                        else "NO_LINKED_SUPPLIER"
                    ),
                )
            ],
        ),
    )


def workflow(builder: AsyncMock) -> AutoReorderSourcingWorkflow:
    return AutoReorderSourcingWorkflow(
        settings(),
        InMemorySaver(),
        review_builder=builder,
        clock=lambda: NOW,
    )


@pytest.mark.anyio
async def test_webhook_source_starts_and_pauses_review() -> None:
    builder = AsyncMock(return_value=review_response())
    graph = workflow(builder)

    snapshot = await graph.start(
        request(),
        source_id="erpnext-auto-reorder-webhook",
    )

    assert snapshot.status == "AWAITING_REVIEW"
    assert snapshot.waiting_for_review is True
    assert snapshot.review is not None
    assert snapshot.review.assessment.outcome == "SUPPLIER_LINK_REVIEW_REQUIRED"
    assert snapshot.assessment_hash is not None
    assert len(snapshot.assessment_hash) == 64


@pytest.mark.anyio
async def test_repeated_event_returns_same_checkpoint() -> None:
    builder = AsyncMock(return_value=review_response())
    graph = workflow(builder)
    first = await graph.start(
        request(),
        source_id="erpnext-auto-reorder-webhook",
    )

    repeated = await graph.start(
        request(),
        source_id="erpnext-auto-reorder-webhook",
    )

    assert repeated == first
    builder.assert_awaited_once()


@pytest.mark.anyio
async def test_repeated_event_retries_an_incomplete_initial_assessment() -> None:
    builder = AsyncMock(
        side_effect=[
            RuntimeError("temporary ERPNext read failure"),
            review_response(),
        ]
    )
    graph = workflow(builder)

    with pytest.raises(RuntimeError, match="temporary ERPNext read failure"):
        await graph.start(
            request(),
            source_id="erpnext-auto-reorder-webhook",
        )

    recovered = await graph.start(
        request(),
        source_id="erpnext-auto-reorder-webhook",
    )

    assert recovered.status == "AWAITING_REVIEW"
    assert recovered.waiting_for_review is True
    assert builder.await_count == 2


@pytest.mark.anyio
async def test_approved_review_revalidates_and_performs_no_write() -> None:
    builder = AsyncMock(return_value=review_response(suppliers=["Supplier A"]))
    graph = workflow(builder)
    waiting = await graph.start(
        request(),
        source_id="erpnext-auto-reorder-webhook",
    )

    approved = await graph.decide(
        REVIEW_ID,
        assessment_hash=waiting.assessment_hash or "",
        approver_id="approver-1",
        decision="approve",
        reason="Supplier linkage reviewed",
    )

    assert approved.status == "REVIEWED_NO_WRITE"
    assert approved.waiting_for_review is False
    assert approved.revalidation_assessment_hash == waiting.assessment_hash
    assert builder.await_count == 2


@pytest.mark.anyio
async def test_changed_supplier_data_invalidates_review() -> None:
    builder = AsyncMock(
        side_effect=[
            review_response(),
            review_response(suppliers=["Supplier A"]),
        ]
    )
    graph = workflow(builder)
    waiting = await graph.start(
        request(),
        source_id="erpnext-auto-reorder-webhook",
    )

    stale = await graph.decide(
        REVIEW_ID,
        assessment_hash=waiting.assessment_hash or "",
        approver_id="approver-1",
        decision="approve",
    )

    assert stale.status == "STALE_REVIEW_REQUIRED"
    assert stale.revalidation_assessment_hash != waiting.assessment_hash


@pytest.mark.anyio
async def test_ineligible_material_request_invalidates_approval() -> None:
    builder = AsyncMock(
        side_effect=[
            review_response(),
            SourcingReviewScopeError("Material Request was cancelled"),
        ]
    )
    graph = workflow(builder)
    waiting = await graph.start(
        request(),
        source_id="erpnext-auto-reorder-webhook",
    )

    stale = await graph.decide(
        REVIEW_ID,
        assessment_hash=waiting.assessment_hash or "",
        approver_id="approver-1",
        decision="approve",
    )

    assert stale.status == "STALE_REVIEW_REQUIRED"
    assert "no longer an eligible" in (stale.status_reason or "")


@pytest.mark.anyio
async def test_rejection_is_terminal() -> None:
    graph = workflow(AsyncMock(return_value=review_response()))
    waiting = await graph.start(
        request(),
        source_id="erpnext-auto-reorder-webhook",
    )
    rejected = await graph.decide(
        REVIEW_ID,
        assessment_hash=waiting.assessment_hash or "",
        approver_id="approver-1",
        decision="reject",
    )

    assert rejected.status == "REJECTED"
    with pytest.raises(SourcingReviewStateError):
        await graph.decide(
            REVIEW_ID,
            assessment_hash=waiting.assessment_hash or "",
            approver_id="approver-1",
            decision="reject",
        )


@pytest.mark.anyio
async def test_decision_must_match_assessment_hash() -> None:
    graph = workflow(AsyncMock(return_value=review_response()))
    await graph.start(request(), source_id="erpnext-auto-reorder-webhook")

    with pytest.raises(SourcingReviewApprovalMismatchError):
        await graph.decide(
            REVIEW_ID,
            assessment_hash="0" * 64,
            approver_id="approver-1",
            decision="approve",
        )


@pytest.mark.anyio
async def test_sqlite_review_survives_application_restart(tmp_path) -> None:
    builder = AsyncMock(return_value=review_response())
    configured = settings()
    configured.workflow_checkpoint_path = tmp_path / "workflows.sqlite3"

    async with open_sqlite_sourcing_workflow(
        configured,
        review_builder=builder,
        clock=lambda: NOW,
    ) as first_runtime:
        waiting = await first_runtime.start(
            request(),
            source_id="erpnext-auto-reorder-webhook",
        )

    async with open_sqlite_sourcing_workflow(
        configured,
        review_builder=builder,
        clock=lambda: NOW,
    ) as restarted_runtime:
        restored = await restarted_runtime.get(REVIEW_ID)

    assert restored == waiting
    assert restored.waiting_for_review is True
