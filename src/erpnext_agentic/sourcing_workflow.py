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
from erpnext_agentic.sourcing_review import (
    AutoReorderReviewRequest,
    AutoReorderReviewResponse,
    SourcingReviewScopeError,
    build_auto_reorder_sourcing_review,
)


SourcingReviewStatus = Literal[
    "ASSESSING",
    "AWAITING_REVIEW",
    "APPROVED_PENDING_REVALIDATION",
    "REVIEWED_NO_WRITE",
    "STALE_REVIEW_REQUIRED",
    "REJECTED",
]
ReviewChoice = Literal["approve", "reject"]


class SourcingReviewNotFoundError(LookupError):
    """Raised when no sourcing-review checkpoint exists."""


class SourcingReviewConflictError(RuntimeError):
    """Raised when a review ID is reused for different source intent."""


class SourcingReviewStateError(RuntimeError):
    """Raised when a decision is invalid for the current review state."""


class SourcingReviewApprovalMismatchError(RuntimeError):
    """Raised when a decision does not match the checkpointed assessment."""


class SourcingReviewSelfApprovalError(RuntimeError):
    """Raised when the event source attempts to approve its own review."""


class SourcingReviewState(TypedDict, total=False):
    """JSON-safe LangGraph state for one ERPNext auto-reorder request."""

    request: dict[str, Any]
    source_id: str
    status: SourcingReviewStatus
    review: dict[str, Any]
    assessment_hash: str
    review_requested_at: str
    review_expires_at: str
    decision: dict[str, Any]
    revalidation_review: dict[str, Any]
    revalidation_assessment_hash: str
    revalidated_at: str
    status_reason: str


class SourcingReviewPrompt(BaseModel):
    """Serializable information surfaced by the LangGraph interrupt."""

    review_id: UUID
    assessment_hash: str
    source_id: str
    expires_at: datetime
    review: AutoReorderReviewResponse


class SourcingReviewDecision(BaseModel):
    """Trusted decision metadata supplied by the platform approval endpoint."""

    model_config = ConfigDict(extra="forbid")

    review_id: UUID
    assessment_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approver_id: str = Field(min_length=1)
    decision: ReviewChoice
    decided_at: datetime
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("decided_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision timestamp must include timezone information")
        return value


class SourcingReviewSnapshot(BaseModel):
    """Application-facing view of one checkpointed sourcing review."""

    review_id: UUID
    source_id: str
    status: SourcingReviewStatus
    review: AutoReorderReviewResponse | None = None
    assessment_hash: str | None = None
    review_requested_at: datetime | None = None
    review_expires_at: datetime | None = None
    decision: SourcingReviewDecision | None = None
    revalidation_review: AutoReorderReviewResponse | None = None
    revalidation_assessment_hash: str | None = None
    revalidated_at: datetime | None = None
    status_reason: str | None = None
    waiting_for_review: bool


class SourcingReviewBuilder(Protocol):
    async def __call__(
        self,
        settings: Settings,
        request: AutoReorderReviewRequest,
        *,
        source_id: str,
    ) -> AutoReorderReviewResponse: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("sourcing-review clock must include timezone information")
    return value.astimezone(timezone.utc)


def _assessment_hash(review: dict[str, Any]) -> str:
    canonical = json.dumps(
        review,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _thread_config(review_id: UUID) -> dict[str, dict[str, str]]:
    return {
        "configurable": {
            "thread_id": f"auto-reorder-sourcing-v1:{review_id}",
        }
    }


def build_auto_reorder_sourcing_workflow(
    settings: Settings,
    checkpointer: BaseCheckpointSaver,
    *,
    review_builder: SourcingReviewBuilder = build_auto_reorder_sourcing_review,
    clock: Callable[[], datetime] = _utc_now,
) -> CompiledStateGraph:
    """Compile the event-driven review graph without an ERPNext write node."""

    async def build_review(state: SourcingReviewState) -> SourcingReviewState:
        request = AutoReorderReviewRequest.model_validate(state["request"])
        review = await review_builder(
            settings,
            request,
            source_id=state["source_id"],
        )
        review_data = review.model_dump(mode="json")
        requested_at = _as_utc(clock())
        expires_at = requested_at + timedelta(
            minutes=settings.approval_ttl_minutes
        )
        return {
            "review": review_data,
            "assessment_hash": _assessment_hash(review_data),
            "status": "AWAITING_REVIEW",
            "review_requested_at": requested_at.isoformat(),
            "review_expires_at": expires_at.isoformat(),
        }

    def request_human_review(state: SourcingReviewState) -> SourcingReviewState:
        prompt = SourcingReviewPrompt(
            review_id=UUID(state["request"]["review_id"]),
            assessment_hash=state["assessment_hash"],
            source_id=state["source_id"],
            expires_at=datetime.fromisoformat(state["review_expires_at"]),
            review=AutoReorderReviewResponse.model_validate(state["review"]),
        )
        decision = SourcingReviewDecision.model_validate(
            interrupt(prompt.model_dump(mode="json"))
        )

        if decision.review_id != prompt.review_id or not compare_digest(
            decision.assessment_hash,
            prompt.assessment_hash,
        ):
            raise SourcingReviewApprovalMismatchError(
                "Decision does not match the checkpointed sourcing assessment"
            )
        if (
            not settings.allow_self_approval
            and decision.approver_id == state["source_id"]
        ):
            raise SourcingReviewSelfApprovalError(
                "Event source and sourcing reviewer must be different identities"
            )

        decision_time = decision.decided_at.astimezone(timezone.utc)
        expires_at = prompt.expires_at.astimezone(timezone.utc)
        result: SourcingReviewState = {
            "decision": decision.model_dump(mode="json")
        }
        if decision_time > expires_at:
            result.update(
                {
                    "status": "REJECTED",
                    "status_reason": "Sourcing review expired before the decision",
                }
            )
        elif decision.decision == "reject":
            result["status"] = "REJECTED"
        else:
            result["status"] = "APPROVED_PENDING_REVALIDATION"
        return result

    def route_after_review(state: SourcingReviewState) -> str:
        if state["status"] == "APPROVED_PENDING_REVALIDATION":
            return "revalidate_review"
        return "finish"

    async def revalidate_review(
        state: SourcingReviewState,
    ) -> SourcingReviewState:
        request = AutoReorderReviewRequest.model_validate(state["request"])
        try:
            current_review = await review_builder(
                settings,
                request,
                source_id=state["source_id"],
            )
        except SourcingReviewScopeError:
            return {
                "revalidated_at": _as_utc(clock()).isoformat(),
                "status": "STALE_REVIEW_REQUIRED",
                "status_reason": (
                    "ERPNext Material Request is no longer an eligible submitted "
                    "automatic Purchase reorder"
                ),
            }
        current_data = current_review.model_dump(mode="json")
        current_hash = _assessment_hash(current_data)
        result: SourcingReviewState = {
            "revalidation_review": current_data,
            "revalidation_assessment_hash": current_hash,
            "revalidated_at": _as_utc(clock()).isoformat(),
        }
        if not compare_digest(current_hash, state["assessment_hash"]):
            result.update(
                {
                    "status": "STALE_REVIEW_REQUIRED",
                    "status_reason": (
                        "ERPNext sourcing data changed after review; start a new review"
                    ),
                }
            )
            return result

        result.update(
            {
                "status": "REVIEWED_NO_WRITE",
                "status_reason": (
                    "Sourcing assessment revalidated; downstream ERPNext mutation "
                    "is not implemented"
                ),
            }
        )
        return result

    graph = StateGraph(SourcingReviewState)
    graph.add_node("build_review", build_review)
    graph.add_node("request_human_review", request_human_review)
    graph.add_node("revalidate_review", revalidate_review)
    graph.add_edge(START, "build_review")
    graph.add_edge("build_review", "request_human_review")
    graph.add_conditional_edges(
        "request_human_review",
        route_after_review,
        {
            "revalidate_review": "revalidate_review",
            "finish": END,
        },
    )
    graph.add_edge("revalidate_review", END)
    return graph.compile(
        checkpointer=checkpointer,
        name="auto_reorder_sourcing_review",
    )


class AutoReorderSourcingWorkflow:
    """Safe facade around the event-driven sourcing-review graph."""

    def __init__(
        self,
        settings: Settings,
        checkpointer: BaseCheckpointSaver,
        *,
        review_builder: SourcingReviewBuilder = build_auto_reorder_sourcing_review,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._graph = build_auto_reorder_sourcing_workflow(
            settings,
            checkpointer,
            review_builder=review_builder,
            clock=clock,
        )

    async def start(
        self,
        request: AutoReorderReviewRequest,
        *,
        source_id: str,
    ) -> SourcingReviewSnapshot:
        """Start once, or return the review already bound to this event."""

        config = _thread_config(request.review_id)
        existing = await self._graph.aget_state(config)
        if existing.values:
            saved_request = AutoReorderReviewRequest.model_validate(
                existing.values["request"]
            )
            if (
                saved_request != request
                or existing.values["source_id"] != source_id
            ):
                raise SourcingReviewConflictError(
                    "review_id is already bound to a different ERPNext event"
                )
            if existing.values["status"] == "ASSESSING":
                await self._graph.ainvoke(None, config=config)
                return await self.get(request.review_id)
            return self._snapshot(existing)

        initial_state: SourcingReviewState = {
            "request": request.model_dump(mode="json"),
            "source_id": source_id,
            "status": "ASSESSING",
        }
        await self._graph.ainvoke(initial_state, config=config)
        return await self.get(request.review_id)

    async def decide(
        self,
        review_id: UUID,
        *,
        assessment_hash: str,
        approver_id: str,
        decision: ReviewChoice,
        reason: str | None = None,
    ) -> SourcingReviewSnapshot:
        """Resume a matching review; this never writes to ERPNext."""

        current = await self.get(review_id)
        if current.assessment_hash is None or not compare_digest(
            current.assessment_hash,
            assessment_hash,
        ):
            raise SourcingReviewApprovalMismatchError(
                "Decision does not match the checkpointed sourcing assessment"
            )
        if (
            not self._settings.allow_self_approval
            and approver_id == current.source_id
        ):
            raise SourcingReviewSelfApprovalError(
                "Event source and sourcing reviewer must be different identities"
            )

        if current.status == "APPROVED_PENDING_REVALIDATION":
            recorded = current.decision
            if (
                recorded is None
                or recorded.approver_id != approver_id
                or recorded.decision != decision
                or recorded.reason != reason
            ):
                raise SourcingReviewStateError(
                    "Revalidation retry must match the recorded review decision"
                )
            await self._graph.ainvoke(None, config=_thread_config(review_id))
            return await self.get(review_id)

        if not current.waiting_for_review or current.status != "AWAITING_REVIEW":
            raise SourcingReviewStateError("Sourcing workflow is not awaiting review")

        review_decision = SourcingReviewDecision(
            review_id=review_id,
            assessment_hash=assessment_hash,
            approver_id=approver_id,
            decision=decision,
            decided_at=_as_utc(self._clock()),
            reason=reason,
        )
        await self._graph.ainvoke(
            Command(resume=review_decision.model_dump(mode="json")),
            config=_thread_config(review_id),
        )
        return await self.get(review_id)

    async def get(self, review_id: UUID) -> SourcingReviewSnapshot:
        """Read the latest checkpoint for one sourcing review."""

        state = await self._graph.aget_state(_thread_config(review_id))
        if not state.values:
            raise SourcingReviewNotFoundError(
                f"Unknown sourcing review: {review_id}"
            )
        return self._snapshot(state)

    @staticmethod
    def _snapshot(state: Any) -> SourcingReviewSnapshot:
        values: SourcingReviewState = state.values
        request = AutoReorderReviewRequest.model_validate(values["request"])
        return SourcingReviewSnapshot(
            review_id=request.review_id,
            source_id=values["source_id"],
            status=values["status"],
            review=(
                AutoReorderReviewResponse.model_validate(values["review"])
                if "review" in values
                else None
            ),
            assessment_hash=values.get("assessment_hash"),
            review_requested_at=values.get("review_requested_at"),
            review_expires_at=values.get("review_expires_at"),
            decision=(
                SourcingReviewDecision.model_validate(values["decision"])
                if "decision" in values
                else None
            ),
            revalidation_review=(
                AutoReorderReviewResponse.model_validate(
                    values["revalidation_review"]
                )
                if "revalidation_review" in values
                else None
            ),
            revalidation_assessment_hash=values.get(
                "revalidation_assessment_hash"
            ),
            revalidated_at=values.get("revalidated_at"),
            status_reason=values.get("status_reason"),
            waiting_for_review=bool(state.interrupts),
        )


@asynccontextmanager
async def open_sqlite_sourcing_workflow(
    settings: Settings,
    *,
    review_builder: SourcingReviewBuilder = build_auto_reorder_sourcing_review,
    clock: Callable[[], datetime] = _utc_now,
) -> AsyncIterator[AutoReorderSourcingWorkflow]:
    """Open the local durable checkpointer for sourcing reviews."""

    checkpoint_path = Path(settings.workflow_checkpoint_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        yield AutoReorderSourcingWorkflow(
            settings,
            saver,
            review_builder=review_builder,
            clock=clock,
        )
