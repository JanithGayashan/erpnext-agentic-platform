from collections.abc import AsyncIterator
from hmac import compare_digest
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from erpnext_agentic.config import Settings, get_settings
from erpnext_agentic.erpnext_client import (
    ERPNextConfigurationError,
    ERPNextDataError,
)
from erpnext_agentic.proposal_service import (
    ProposalRequest,
    ProposalResponse,
    ProposalScopeError,
    build_replenishment_proposal,
)
from erpnext_agentic.workflow import (
    ApprovalChoice,
    ApprovalMismatchError,
    ReplenishmentApprovalWorkflow,
    SelfApprovalError,
    WorkflowConflictError,
    WorkflowNotFoundError,
    WorkflowSnapshot,
    WorkflowStateError,
    open_sqlite_workflow,
)


class HealthResponse(BaseModel):
    """Public response returned when this service is running."""

    status: str
    service: str
    environment: str


class PlatformActor(BaseModel):
    """Authenticated identity allowed to inspect a workflow."""

    actor_id: str
    role: Literal["requester", "approver"]


class WorkflowDecisionRequest(BaseModel):
    """Approval input; identity and timestamp come from the server."""

    model_config = ConfigDict(extra="forbid")

    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: ApprovalChoice
    reason: str | None = Field(default=None, max_length=500)


def create_app(
    settings: Settings | None = None,
    *,
    workflow: ReplenishmentApprovalWorkflow | None = None,
) -> FastAPI:
    """Build the FastAPI application with explicit configuration."""

    resolved_settings = settings or get_settings()
    application = FastAPI(
        title=resolved_settings.name,
        version="0.1.0",
    )
    platform_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def require_configured_key(
        configured_api_key: SecretStr | None,
        *,
        unavailable_detail: str,
    ) -> str:
        if configured_api_key is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=unavailable_detail,
            )
        secret_value = configured_api_key.get_secret_value()
        if not secret_value:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=unavailable_detail,
            )
        return secret_value

    async def require_requester(
        supplied_api_key: Annotated[str | None, Depends(platform_api_key_header)],
    ) -> str:
        configured_api_key = require_configured_key(
            resolved_settings.platform_api_key,
            unavailable_detail="Platform API authentication is not configured",
        )
        if supplied_api_key is None or not compare_digest(
            supplied_api_key,
            configured_api_key,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid platform API key",
            )
        return resolved_settings.platform_requester_id

    async def require_approver(
        supplied_api_key: Annotated[str | None, Depends(platform_api_key_header)],
    ) -> str:
        configured_api_key = require_configured_key(
            resolved_settings.platform_approver_api_key,
            unavailable_detail="Platform approver authentication is not configured",
        )
        if supplied_api_key is None or not compare_digest(
            supplied_api_key,
            configured_api_key,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid platform approver API key",
            )
        return resolved_settings.platform_approver_id

    async def require_workflow_actor(
        supplied_api_key: Annotated[str | None, Depends(platform_api_key_header)],
    ) -> PlatformActor:
        requester_key = resolved_settings.platform_api_key
        approver_key = resolved_settings.platform_approver_api_key
        configured = [
            candidate
            for candidate in (requester_key, approver_key)
            if candidate is not None and candidate.get_secret_value()
        ]
        if not configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Platform workflow authentication is not configured",
            )
        if supplied_api_key is not None:
            if (
                requester_key is not None
                and requester_key.get_secret_value()
                and compare_digest(
                    supplied_api_key,
                    requester_key.get_secret_value(),
                )
            ):
                return PlatformActor(
                    actor_id=resolved_settings.platform_requester_id,
                    role="requester",
                )
            if (
                approver_key is not None
                and approver_key.get_secret_value()
                and compare_digest(
                    supplied_api_key,
                    approver_key.get_secret_value(),
                )
            ):
                return PlatformActor(
                    actor_id=resolved_settings.platform_approver_id,
                    role="approver",
                )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid platform workflow API key",
        )

    async def provide_workflow() -> AsyncIterator[ReplenishmentApprovalWorkflow]:
        if workflow is not None:
            yield workflow
            return
        async with open_sqlite_workflow(resolved_settings) as durable_workflow:
            yield durable_workflow

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service=resolved_settings.name,
            environment=resolved_settings.environment,
        )

    @application.post(
        "/replenishment/proposals",
        response_model=ProposalResponse,
        tags=["replenishment"],
    )
    async def create_replenishment_proposal(
        request: ProposalRequest,
        requester_id: Annotated[str, Depends(require_requester)],
    ) -> ProposalResponse:
        try:
            return await build_replenishment_proposal(
                resolved_settings,
                request,
                requester_id=requester_id,
            )
        except ProposalScopeError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from None
        except ERPNextConfigurationError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ERPNext integration is not configured",
            ) from None
        except (ERPNextDataError, ValidationError, httpx.HTTPError):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="ERPNext read or response validation failed safely",
            ) from None

    @application.post(
        "/replenishment/workflows",
        response_model=WorkflowSnapshot,
        tags=["replenishment"],
    )
    async def start_replenishment_workflow(
        request: ProposalRequest,
        requester_id: Annotated[str, Depends(require_requester)],
        active_workflow: Annotated[
            ReplenishmentApprovalWorkflow,
            Depends(provide_workflow),
        ],
    ) -> WorkflowSnapshot:
        try:
            return await active_workflow.start(
                request,
                requester_id=requester_id,
            )
        except WorkflowConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from None
        except ProposalScopeError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from None
        except ERPNextConfigurationError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ERPNext integration is not configured",
            ) from None
        except (ERPNextDataError, ValidationError, httpx.HTTPError):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="ERPNext read or response validation failed safely",
            ) from None

    @application.get(
        "/replenishment/workflows/{request_id}",
        response_model=WorkflowSnapshot,
        tags=["replenishment"],
    )
    async def get_replenishment_workflow(
        request_id: UUID,
        _actor: Annotated[PlatformActor, Depends(require_workflow_actor)],
        active_workflow: Annotated[
            ReplenishmentApprovalWorkflow,
            Depends(provide_workflow),
        ],
    ) -> WorkflowSnapshot:
        try:
            return await active_workflow.get(request_id)
        except WorkflowNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from None

    @application.post(
        "/replenishment/workflows/{request_id}/decision",
        response_model=WorkflowSnapshot,
        tags=["replenishment"],
    )
    async def decide_replenishment_workflow(
        request_id: UUID,
        request: WorkflowDecisionRequest,
        approver_id: Annotated[str, Depends(require_approver)],
        active_workflow: Annotated[
            ReplenishmentApprovalWorkflow,
            Depends(provide_workflow),
        ],
    ) -> WorkflowSnapshot:
        try:
            return await active_workflow.decide(
                request_id,
                plan_hash=request.plan_hash,
                approver_id=approver_id,
                decision=request.decision,
                reason=request.reason,
            )
        except WorkflowNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from None
        except ApprovalMismatchError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from None
        except SelfApprovalError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from None
        except WorkflowStateError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from None
        except ERPNextConfigurationError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ERPNext integration is not configured",
            ) from None
        except (ERPNextDataError, ValidationError, httpx.HTTPError):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="ERPNext revalidation failed safely; retry the same decision",
            ) from None

    return application


app = create_app()
