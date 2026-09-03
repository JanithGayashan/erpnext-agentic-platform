from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from hmac import compare_digest
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field, field_validator

from erpnext_agentic.config import Settings
from erpnext_agentic.proposal_service import (
    ProposalRequest,
    ProposalResponse,
    build_replenishment_proposal,
)


WorkflowStatus = Literal[
    "PLANNING",
    "AWAITING_APPROVAL",
    "NO_ACTION",
    "APPROVED_PENDING_REVALIDATION",
    "APPROVED_NO_WRITE",
    "STALE_REPLAN_REQUIRED",
    "REJECTED",
]
ApprovalChoice = Literal["approve", "reject"]


class WorkflowNotFoundError(LookupError):
    """Raised when no checkpoint exists for a request ID."""


class WorkflowConflictError(RuntimeError):
    """Raised when a request ID is reused for different intent or identity."""


class WorkflowStateError(RuntimeError):
    """Raised when a command is invalid for the workflow's current state."""


class ApprovalMismatchError(RuntimeError):
    """Raised when a decision does not reference the checkpointed plan."""


class SelfApprovalError(RuntimeError):
    """Raised when the requester attempts to approve their own plan."""


class WorkflowState(TypedDict, total=False):
    """JSON-safe state persisted by LangGraph for one request ID."""

    request: dict[str, Any]
    requester_id: str
    status: WorkflowStatus
    proposal: dict[str, Any]
    plan_hash: str
    approval_requested_at: str
    approval_expires_at: str
    approval: dict[str, Any]
    revalidation_proposal: dict[str, Any]
    revalidation_plan_hash: str
    revalidated_at: str
    status_reason: str


class ApprovalPrompt(BaseModel):
    """Serializable information surfaced by the LangGraph interrupt."""

    request_id: UUID
    plan_hash: str
    requester_id: str
    expires_at: datetime
    proposal: ProposalResponse


class ApprovalDecision(BaseModel):
    """Trusted decision metadata supplied when the workflow is resumed."""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    plan_hash: str = Field(min_length=64, max_length=64)
    approver_id: str = Field(min_length=1)
    decision: ApprovalChoice
    decided_at: datetime
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("decided_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision timestamp must include timezone information")
        return value


class ApprovalRecord(ApprovalDecision):
    """Decision retained in the workflow checkpoint."""


class WorkflowSnapshot(BaseModel):
    """Application-facing view of one checkpointed workflow."""

    request_id: UUID
    requester_id: str
    status: WorkflowStatus
    proposal: ProposalResponse | None = None
    plan_hash: str | None = None
    approval_requested_at: datetime | None = None
    approval_expires_at: datetime | None = None
    approval: ApprovalRecord | None = None
    revalidation_proposal: ProposalResponse | None = None
    revalidation_plan_hash: str | None = None
    revalidated_at: datetime | None = None
    status_reason: str | None = None
    waiting_for_approval: bool


class ProposalBuilder(Protocol):
    async def __call__(
        self,
        settings: Settings,
        request: ProposalRequest,
        *,
        requester_id: str,
    ) -> ProposalResponse: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("workflow clock must include timezone information")
    return value.astimezone(timezone.utc)


def _canonical_plan_hash(proposal: dict[str, Any]) -> str:
    canonical = json.dumps(
        proposal,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _thread_config(request_id: UUID) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": str(request_id)}}


def build_replenishment_workflow(
    settings: Settings,
    checkpointer: BaseCheckpointSaver,
    *,
    proposal_builder: ProposalBuilder = build_replenishment_proposal,
    clock: Callable[[], datetime] = _utc_now,
) -> CompiledStateGraph:
    """Compile the first resumable workflow without any ERPNext write node."""

    async def build_proposal(state: WorkflowState) -> WorkflowState:
        request = ProposalRequest.model_validate(state["request"])
        proposal = await proposal_builder(
            settings,
            request,
            requester_id=state["requester_id"],
        )
        proposal_data = proposal.model_dump(mode="json")
        result: WorkflowState = {
            "proposal": proposal_data,
            "plan_hash": _canonical_plan_hash(proposal_data),
        }
        if proposal.proposal.outcome == "NO_ACTION":
            result["status"] = "NO_ACTION"
            return result

        requested_at = _as_utc(clock())
        expires_at = requested_at + timedelta(
            minutes=settings.approval_ttl_minutes
        )
        result.update(
            {
                "status": "AWAITING_APPROVAL",
                "approval_requested_at": requested_at.isoformat(),
                "approval_expires_at": expires_at.isoformat(),
            }
        )
        return result

    def route_after_proposal(state: WorkflowState) -> str:
        if state["status"] == "NO_ACTION":
            return "finish"
        return "request_approval"

    def request_approval(state: WorkflowState) -> WorkflowState:
        prompt = ApprovalPrompt(
            request_id=UUID(state["request"]["request_id"]),
            plan_hash=state["plan_hash"],
            requester_id=state["requester_id"],
            expires_at=datetime.fromisoformat(state["approval_expires_at"]),
            proposal=ProposalResponse.model_validate(state["proposal"]),
        )
        decision = ApprovalDecision.model_validate(
            interrupt(prompt.model_dump(mode="json"))
        )

        if decision.request_id != prompt.request_id or not compare_digest(
            decision.plan_hash,
            prompt.plan_hash,
        ):
            raise ApprovalMismatchError(
                "Approval does not match the checkpointed request and plan"
            )
        if (
            not settings.allow_self_approval
            and decision.approver_id == state["requester_id"]
        ):
            raise SelfApprovalError(
                "Requester and approver must be different identities"
            )

        decision_time = decision.decided_at.astimezone(timezone.utc)
        expires_at = prompt.expires_at.astimezone(timezone.utc)
        result: WorkflowState = {
            "approval": decision.model_dump(mode="json"),
        }
        if decision_time > expires_at:
            result.update(
                {
                    "status": "REJECTED",
                    "status_reason": "Approval expired before the decision",
                }
            )
        elif decision.decision == "reject":
            result["status"] = "REJECTED"
        else:
            result["status"] = "APPROVED_PENDING_REVALIDATION"
        return result

    def route_after_approval(state: WorkflowState) -> str:
        if state["status"] == "APPROVED_PENDING_REVALIDATION":
            return "revalidate_proposal"
        return "finish"

    async def revalidate_proposal(state: WorkflowState) -> WorkflowState:
        request = ProposalRequest.model_validate(state["request"])
        current_proposal = await proposal_builder(
            settings,
            request,
            requester_id=state["requester_id"],
        )
        current_data = current_proposal.model_dump(mode="json")
        current_hash = _canonical_plan_hash(current_data)
        result: WorkflowState = {
            "revalidation_proposal": current_data,
            "revalidation_plan_hash": current_hash,
            "revalidated_at": _as_utc(clock()).isoformat(),
        }
        if not compare_digest(current_hash, state["plan_hash"]):
            result.update(
                {
                    "status": "STALE_REPLAN_REQUIRED",
                    "status_reason": (
                        "ERPNext data changed after approval; create a new plan"
                    ),
                }
            )
            return result

        result.update(
            {
                "status": "APPROVED_NO_WRITE",
                "status_reason": (
                    "Approved plan revalidated; ERPNext mutation is not implemented"
                ),
            }
        )
        return result

    graph = StateGraph(WorkflowState)
    graph.add_node("build_proposal", build_proposal)
    graph.add_node("request_approval", request_approval)
    graph.add_node("revalidate_proposal", revalidate_proposal)
    graph.add_edge(START, "build_proposal")
    graph.add_conditional_edges(
        "build_proposal",
        route_after_proposal,
        {
            "request_approval": "request_approval",
            "finish": END,
        },
    )
    graph.add_conditional_edges(
        "request_approval",
        route_after_approval,
        {
            "revalidate_proposal": "revalidate_proposal",
            "finish": END,
        },
    )
    graph.add_edge("revalidate_proposal", END)
    return graph.compile(
        checkpointer=checkpointer,
        name="replenishment_approval",
    )


class ReplenishmentApprovalWorkflow:
    """Safe application facade around the checkpointed LangGraph graph."""

    def __init__(
        self,
        settings: Settings,
        checkpointer: BaseCheckpointSaver,
        *,
        proposal_builder: ProposalBuilder = build_replenishment_proposal,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._graph = build_replenishment_workflow(
            settings,
            checkpointer,
            proposal_builder=proposal_builder,
            clock=clock,
        )

    async def start(
        self,
        request: ProposalRequest,
        *,
        requester_id: str,
    ) -> WorkflowSnapshot:
        """Start once, or return the existing workflow for the same intent."""

        existing = await self._graph.aget_state(_thread_config(request.request_id))
        if existing.values:
            saved_request = ProposalRequest.model_validate(
                existing.values["request"]
            )
            if saved_request != request or existing.values["requester_id"] != requester_id:
                raise WorkflowConflictError(
                    "request_id is already bound to different intent or identity"
                )
            return self._snapshot(existing)

        initial_state: WorkflowState = {
            "request": request.model_dump(mode="json"),
            "requester_id": requester_id,
            "status": "PLANNING",
        }
        await self._graph.ainvoke(
            initial_state,
            config=_thread_config(request.request_id),
        )
        return await self.get(request.request_id)

    async def decide(
        self,
        request_id: UUID,
        *,
        plan_hash: str,
        approver_id: str,
        decision: ApprovalChoice,
        reason: str | None = None,
    ) -> WorkflowSnapshot:
        """Resume a matching approval interrupt; this never writes to ERPNext."""

        current = await self.get(request_id)
        if current.plan_hash is None or not compare_digest(
            current.plan_hash,
            plan_hash,
        ):
            raise ApprovalMismatchError(
                "Approval does not match the checkpointed plan"
            )
        if (
            not self._settings.allow_self_approval
            and approver_id == current.requester_id
        ):
            raise SelfApprovalError(
                "Requester and approver must be different identities"
            )

        if current.status == "APPROVED_PENDING_REVALIDATION":
            recorded = current.approval
            if (
                recorded is None
                or recorded.approver_id != approver_id
                or recorded.decision != decision
                or recorded.reason != reason
            ):
                raise WorkflowStateError(
                    "Revalidation retry must match the recorded approval"
                )
            await self._graph.ainvoke(
                None,
                config=_thread_config(request_id),
            )
            return await self.get(request_id)

        if not current.waiting_for_approval or current.status != "AWAITING_APPROVAL":
            raise WorkflowStateError("Workflow is not awaiting approval")

        approval = ApprovalDecision(
            request_id=request_id,
            plan_hash=plan_hash,
            approver_id=approver_id,
            decision=decision,
            decided_at=_as_utc(self._clock()),
            reason=reason,
        )
        await self._graph.ainvoke(
            Command(resume=approval.model_dump(mode="json")),
            config=_thread_config(request_id),
        )
        return await self.get(request_id)

    async def get(self, request_id: UUID) -> WorkflowSnapshot:
        """Read the latest checkpoint for one request ID."""

        state = await self._graph.aget_state(_thread_config(request_id))
        if not state.values:
            raise WorkflowNotFoundError(f"Unknown workflow: {request_id}")
        return self._snapshot(state)

    @staticmethod
    def _snapshot(state: Any) -> WorkflowSnapshot:
        values: WorkflowState = state.values
        request = ProposalRequest.model_validate(values["request"])
        return WorkflowSnapshot(
            request_id=request.request_id,
            requester_id=values["requester_id"],
            status=values["status"],
            proposal=(
                ProposalResponse.model_validate(values["proposal"])
                if "proposal" in values
                else None
            ),
            plan_hash=values.get("plan_hash"),
            approval_requested_at=values.get("approval_requested_at"),
            approval_expires_at=values.get("approval_expires_at"),
            approval=(
                ApprovalRecord.model_validate(values["approval"])
                if "approval" in values
                else None
            ),
            revalidation_proposal=(
                ProposalResponse.model_validate(values["revalidation_proposal"])
                if "revalidation_proposal" in values
                else None
            ),
            revalidation_plan_hash=values.get("revalidation_plan_hash"),
            revalidated_at=values.get("revalidated_at"),
            status_reason=values.get("status_reason"),
            waiting_for_approval=bool(state.interrupts),
        )


@asynccontextmanager
async def open_sqlite_workflow(
    settings: Settings,
    *,
    proposal_builder: ProposalBuilder = build_replenishment_proposal,
    clock: Callable[[], datetime] = _utc_now,
) -> AsyncIterator[ReplenishmentApprovalWorkflow]:
    """Open the local durable checkpointer used by the development service."""

    checkpoint_path = Path(settings.workflow_checkpoint_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        yield ReplenishmentApprovalWorkflow(
            settings,
            saver,
            proposal_builder=proposal_builder,
            clock=clock,
        )
