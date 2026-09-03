"""Run a non-mutating live smoke check through FastAPI and LangGraph."""

import asyncio
import secrets
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

import httpx

from erpnext_agentic.config import Settings
from erpnext_agentic.main import create_app


class WorkflowSmokeCheckError(RuntimeError):
    """Raised when a smoke-check HTTP response fails safely."""


def _require_success(response: httpx.Response, step: str) -> dict[str, Any]:
    if response.is_success:
        return response.json()
    try:
        detail = response.json().get("detail", "No safe error detail")
    except ValueError:
        detail = "Response was not valid JSON"
    raise WorkflowSmokeCheckError(
        f"{step} failed with HTTP {response.status_code}: {detail}"
    )


async def run_check() -> None:
    """Exercise live ERPNext reads and a checkpointed no-write approval."""

    base_settings = Settings()
    requester_key = secrets.token_urlsafe(32)
    approver_key = secrets.token_urlsafe(32)
    request_id = uuid4()

    with TemporaryDirectory(prefix="erpnext-agentic-smoke-") as temp_directory:
        settings = Settings.model_validate(
            {
                **base_settings.model_dump(),
                "platform_requester_id": "workflow-smoke-requester",
                "platform_api_key": requester_key,
                "platform_approver_id": "workflow-smoke-approver",
                "platform_approver_api_key": approver_key,
                "workflow_checkpoint_path": Path(temp_directory)
                / "workflows.sqlite3",
            }
        )
        request_body = {
            "request_id": str(request_id),
            "company": settings.erpnext_company,
            "item_code": settings.erpnext_item,
            "warehouse": settings.erpnext_warehouse,
        }
        requester_headers = {"X-API-Key": requester_key}
        approver_headers = {"X-API-Key": approver_key}

        first_app = create_app(settings)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first_app),
            base_url="http://workflow-smoke.local",
        ) as client:
            started = _require_success(
                await client.post(
                    "/replenishment/workflows",
                    json=request_body,
                    headers=requester_headers,
                ),
                "Workflow start",
            )

        if started["status"] != "AWAITING_APPROVAL":
            raise WorkflowSmokeCheckError(
                f"Expected AWAITING_APPROVAL, received {started['status']}"
            )

        restarted_app = create_app(settings)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted_app),
            base_url="http://workflow-smoke.local",
        ) as client:
            restored = _require_success(
                await client.get(
                    f"/replenishment/workflows/{request_id}",
                    headers=approver_headers,
                ),
                "Workflow restore",
            )
            approved = _require_success(
                await client.post(
                    f"/replenishment/workflows/{request_id}/decision",
                    headers=approver_headers,
                    json={
                        "plan_hash": restored["plan_hash"],
                        "decision": "approve",
                        "reason": "Automated no-write smoke check",
                    },
                ),
                "Workflow approval",
            )

        if restored["status"] != "AWAITING_APPROVAL":
            raise WorkflowSmokeCheckError(
                f"Expected restored approval wait, received {restored['status']}"
            )
        if approved["status"] != "APPROVED_NO_WRITE":
            raise WorkflowSmokeCheckError(
                f"Expected APPROVED_NO_WRITE, received {approved['status']}"
            )
        if approved["revalidation_plan_hash"] != started["plan_hash"]:
            raise WorkflowSmokeCheckError(
                "Live ERPNext data changed during approval smoke check"
            )

        proposal = approved["proposal"]["proposal"]
        print(f"Workflow request: {request_id}")
        print(f"Initial state: {started['status']}")
        print(f"State after application restart: {restored['status']}")
        print(f"Decision result: {approved['status']}")
        print("Post-approval ERPNext revalidation: unchanged")
        print(
            "Proposal: "
            f"{proposal['proposed_stock_qty']} {proposal['stock_uom']} "
            f"of {proposal['item_code']}"
        )
        print("ERPNext mutation performed: no")
        print("Temporary credentials and checkpoint removed: yes")


def main() -> None:
    try:
        asyncio.run(run_check())
    except (WorkflowSmokeCheckError, httpx.HTTPError, OSError) as exc:
        raise SystemExit(f"Workflow smoke check failed safely: {exc}") from None


if __name__ == "__main__":
    main()
