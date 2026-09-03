from hmac import compare_digest
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ValidationError

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


class HealthResponse(BaseModel):
    """Public response returned when this service is running."""

    status: str
    service: str
    environment: str


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application with explicit configuration."""

    resolved_settings = settings or get_settings()
    application = FastAPI(
        title=resolved_settings.name,
        version="0.1.0",
    )
    platform_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    async def require_requester(
        supplied_api_key: Annotated[str | None, Depends(platform_api_key_header)],
    ) -> str:
        configured_api_key = resolved_settings.platform_api_key
        if configured_api_key is None or not configured_api_key.get_secret_value():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Platform API authentication is not configured",
            )
        if supplied_api_key is None or not compare_digest(
            supplied_api_key,
            configured_api_key.get_secret_value(),
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid platform API key",
            )
        return resolved_settings.platform_requester_id

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

    return application


app = create_app()
