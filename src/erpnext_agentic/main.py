from fastapi import FastAPI
from pydantic import BaseModel

from erpnext_agentic.config import Settings, get_settings


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

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service=resolved_settings.name,
            environment=resolved_settings.environment,
        )

    return application


app = create_app()

