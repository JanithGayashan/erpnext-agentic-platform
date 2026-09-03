from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from erpnext_agentic.config import Settings
from erpnext_agentic.erpnext_client import ERPNextClient, ERPNextDataError
from erpnext_agentic.replenishment import (
    ReplenishmentInputs,
    ReplenishmentProposal,
    calculate_replenishment,
)


class ProposalScopeError(RuntimeError):
    """Raised when a request is outside the configured first-milestone scope."""


class ProposalRequest(BaseModel):
    """Explicit caller intent; requester identity comes from authentication."""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    company: str = Field(min_length=1)
    item_code: str = Field(min_length=1)
    warehouse: str = Field(min_length=1)


class ProposalResponse(BaseModel):
    """Read-only proposal associated with its authenticated requester."""

    request_id: UUID
    requester_id: str
    proposal: ReplenishmentProposal


class AllowedScope(BaseModel):
    """Configured ERPNext scope for the first milestone."""

    company: str
    item_code: str
    warehouse: str

    @model_validator(mode="after")
    def require_values(self) -> Self:
        if not self.company or not self.item_code or not self.warehouse:
            raise ValueError("configured proposal scope cannot contain empty values")
        return self


def validate_scope(settings: Settings, request: ProposalRequest) -> None:
    """Reject requests outside the one configured development fixture."""

    allowed = AllowedScope(
        company=settings.erpnext_company,
        item_code=settings.erpnext_item,
        warehouse=settings.erpnext_warehouse,
    )
    if (
        request.company != allowed.company
        or request.item_code != allowed.item_code
        or request.warehouse != allowed.warehouse
    ):
        raise ProposalScopeError(
            "Request is outside the configured first-milestone scope"
        )


async def build_replenishment_proposal(
    settings: Settings,
    request: ProposalRequest,
    *,
    requester_id: str,
) -> ProposalResponse:
    """Read validated ERPNext state and calculate a non-authorizing proposal."""

    validate_scope(settings, request)

    async with ERPNextClient(settings) as client:
        logged_user = await client.get_logged_user()
        if logged_user != settings.erpnext_expected_user:
            raise ERPNextDataError("ERPNext returned an unexpected API identity")

        site_time_zone = await client.get_time_zone()
        company = await client.get_company(request.company)
        warehouse = await client.get_warehouse(
            request.warehouse,
            expected_company=company.name,
        )
        item_reorder = await client.get_item_reorder(
            request.item_code,
            warehouse=warehouse.name,
        )
        stock_position = await client.get_stock_position(
            item_reorder.item.name,
            warehouse=warehouse.name,
        )
        uom = await client.get_uom(item_reorder.item.stock_uom)

    proposal = calculate_replenishment(
        ReplenishmentInputs(
            item_code=item_reorder.item.name,
            warehouse=warehouse.name,
            stock_uom=uom.name,
            projected_qty=stock_position.projected_qty,
            reorder_level=item_reorder.reorder_rule.warehouse_reorder_level,
            reorder_qty=item_reorder.reorder_rule.warehouse_reorder_qty,
            whole_number_required=uom.must_be_whole_number,
            lead_time_days=item_reorder.item.lead_time_days,
            site_time_zone=site_time_zone,
        )
    )
    return ProposalResponse(
        request_id=request.request_id,
        requester_id=requester_id,
        proposal=proposal,
    )
