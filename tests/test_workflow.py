from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from erpnext_agentic.config import Settings
from erpnext_agentic.proposal_service import ProposalRequest, ProposalResponse
from erpnext_agentic.replenishment import ReplenishmentProposal
from erpnext_agentic.workflow import (
    ApprovalMismatchError,
    ReplenishmentApprovalWorkflow,
    SelfApprovalError,
    WorkflowConflictError,
    WorkflowStateError,
    open_sqlite_workflow,
)


REQUEST_ID = UUID("55aa9796-0939-4f88-984b-55a71af6658a")
NOW = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def request(*, warehouse: str = "Agentic Test Warehouse - JGD") -> ProposalRequest:
    return ProposalRequest(
        request_id=REQUEST_ID,
        company="JG Investors (Demo)",
        item_code="AGENTIC-TEST-ITEM",
        warehouse=warehouse,
    )


def proposal_response(*, outcome: str = "PROPOSAL") -> ProposalResponse:
    proposed_quantity = Decimal("20") if outcome == "PROPOSAL" else Decimal("0")
    return ProposalResponse(
        request_id=REQUEST_ID,
        requester_id="requester-1",
        proposal=ReplenishmentProposal(
            outcome=outcome,
            item_code="AGENTIC-TEST-ITEM",
            warehouse="Agentic Test Warehouse - JGD",
            stock_uom="Nos",
            projected_qty=Decimal("0") if outcome == "PROPOSAL" else Decimal("11"),
            reorder_level=Decimal("10"),
            configured_reorder_qty=Decimal("20"),
            deficiency=Decimal("10") if outcome == "PROPOSAL" else Decimal("0"),
            proposed_stock_qty=proposed_quantity,
            site_time_zone="Asia/Colombo",
            transaction_date=date(2026, 9, 3),
            schedule_date=date(2026, 9, 6),
            lead_time_days=3,
        ),
    )


def workflow_settings(
    *,
    allow_self_approval: bool = False,
) -> Settings:
    return Settings(
        environment="test",
        allow_self_approval=allow_self_approval,
        approval_ttl_minutes=30,
        erpnext_api_key="fake-key",
        erpnext_api_secret="fake-secret",
    )


def workflow(
    builder: AsyncMock,
    *,
    allow_self_approval: bool = False,
) -> ReplenishmentApprovalWorkflow:
    return ReplenishmentApprovalWorkflow(
        workflow_settings(allow_self_approval=allow_self_approval),
        InMemorySaver(),
        proposal_builder=builder,
        clock=lambda: NOW,
    )


@pytest.mark.anyio
async def test_proposal_pauses_with_durable_typed_state() -> None:
    builder = AsyncMock(return_value=proposal_response())
    graph = workflow(builder)

    snapshot = await graph.start(request(), requester_id="requester-1")

    assert snapshot.status == "AWAITING_APPROVAL"
    assert snapshot.waiting_for_approval is True
    assert snapshot.proposal == proposal_response()
    assert snapshot.plan_hash is not None
    assert len(snapshot.plan_hash) == 64
    assert snapshot.approval_expires_at == NOW + timedelta(minutes=30)
    builder.assert_awaited_once()


@pytest.mark.anyio
async def test_rejection_resumes_to_terminal_state() -> None:
    graph = workflow(AsyncMock(return_value=proposal_response()))
    waiting = await graph.start(request(), requester_id="requester-1")

    rejected = await graph.decide(
        REQUEST_ID,
        plan_hash=waiting.plan_hash or "",
        approver_id="approver-1",
        decision="reject",
        reason="Stock review required",
    )

    assert rejected.status == "REJECTED"
    assert rejected.waiting_for_approval is False
    assert rejected.approval is not None
    assert rejected.approval.reason == "Stock review required"


@pytest.mark.anyio
async def test_approval_stops_before_any_erpnext_write() -> None:
    graph = workflow(AsyncMock(return_value=proposal_response()))
    waiting = await graph.start(request(), requester_id="requester-1")

    approved = await graph.decide(
        REQUEST_ID,
        plan_hash=waiting.plan_hash or "",
        approver_id="approver-1",
        decision="approve",
    )

    assert approved.status == "APPROVED_NO_WRITE"
    assert approved.status_reason == (
        "Approval recorded; ERPNext mutation is not implemented"
    )
    assert approved.waiting_for_approval is False


@pytest.mark.anyio
async def test_no_action_finishes_without_interrupt() -> None:
    graph = workflow(AsyncMock(return_value=proposal_response(outcome="NO_ACTION")))

    snapshot = await graph.start(request(), requester_id="requester-1")

    assert snapshot.status == "NO_ACTION"
    assert snapshot.waiting_for_approval is False


@pytest.mark.anyio
async def test_duplicate_request_returns_existing_checkpoint() -> None:
    builder = AsyncMock(return_value=proposal_response())
    graph = workflow(builder)

    first = await graph.start(request(), requester_id="requester-1")
    second = await graph.start(request(), requester_id="requester-1")

    assert second == first
    builder.assert_awaited_once()


@pytest.mark.anyio
async def test_request_id_cannot_be_reused_for_different_intent() -> None:
    graph = workflow(AsyncMock(return_value=proposal_response()))
    await graph.start(request(), requester_id="requester-1")

    with pytest.raises(WorkflowConflictError):
        await graph.start(
            request(warehouse="Different Warehouse"),
            requester_id="requester-1",
        )


@pytest.mark.anyio
async def test_approval_must_match_exact_plan_hash() -> None:
    graph = workflow(AsyncMock(return_value=proposal_response()))
    await graph.start(request(), requester_id="requester-1")

    with pytest.raises(ApprovalMismatchError):
        await graph.decide(
            REQUEST_ID,
            plan_hash="0" * 64,
            approver_id="approver-1",
            decision="approve",
        )

    unchanged = await graph.get(REQUEST_ID)
    assert unchanged.status == "AWAITING_APPROVAL"


@pytest.mark.anyio
async def test_self_approval_is_denied_by_default() -> None:
    graph = workflow(AsyncMock(return_value=proposal_response()))
    waiting = await graph.start(request(), requester_id="requester-1")

    with pytest.raises(SelfApprovalError):
        await graph.decide(
            REQUEST_ID,
            plan_hash=waiting.plan_hash or "",
            approver_id="requester-1",
            decision="approve",
        )


@pytest.mark.anyio
async def test_expired_approval_is_rejected() -> None:
    times = iter([NOW, NOW + timedelta(minutes=31)])
    graph = ReplenishmentApprovalWorkflow(
        workflow_settings(),
        InMemorySaver(),
        proposal_builder=AsyncMock(return_value=proposal_response()),
        clock=lambda: next(times),
    )
    waiting = await graph.start(request(), requester_id="requester-1")

    expired = await graph.decide(
        REQUEST_ID,
        plan_hash=waiting.plan_hash or "",
        approver_id="approver-1",
        decision="approve",
    )

    assert expired.status == "REJECTED"
    assert expired.status_reason == "Approval expired before the decision"


@pytest.mark.anyio
async def test_terminal_workflow_cannot_be_decided_again() -> None:
    graph = workflow(AsyncMock(return_value=proposal_response()))
    waiting = await graph.start(request(), requester_id="requester-1")
    await graph.decide(
        REQUEST_ID,
        plan_hash=waiting.plan_hash or "",
        approver_id="approver-1",
        decision="reject",
    )

    with pytest.raises(WorkflowStateError):
        await graph.decide(
            REQUEST_ID,
            plan_hash=waiting.plan_hash or "",
            approver_id="approver-1",
            decision="approve",
        )


@pytest.mark.anyio
async def test_sqlite_checkpoint_survives_runtime_restart(tmp_path) -> None:
    builder = AsyncMock(return_value=proposal_response())
    settings = workflow_settings()
    settings.workflow_checkpoint_path = tmp_path / "workflows.sqlite3"

    async with open_sqlite_workflow(
        settings,
        proposal_builder=builder,
        clock=lambda: NOW,
    ) as first_runtime:
        waiting = await first_runtime.start(
            request(),
            requester_id="requester-1",
        )

    async with open_sqlite_workflow(
        settings,
        proposal_builder=builder,
        clock=lambda: NOW,
    ) as restarted_runtime:
        restored = await restarted_runtime.get(REQUEST_ID)
        approved = await restarted_runtime.decide(
            REQUEST_ID,
            plan_hash=restored.plan_hash or "",
            approver_id="approver-1",
            decision="approve",
        )

    assert restored == waiting
    assert approved.status == "APPROVED_NO_WRITE"
    builder.assert_awaited_once()
