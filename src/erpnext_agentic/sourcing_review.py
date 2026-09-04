from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from erpnext_agentic.config import Settings
from erpnext_agentic.erpnext_client import (
    ERPNextClient,
    ERPNextDataError,
    MaterialRequestRecord,
    SourcingItemRecord,
)


AUTO_REORDER_REVIEW_NAMESPACE = UUID("935a920b-915b-4c83-9b75-a7ebf5d482f8")

ItemReadinessOutcome = Literal["LINKED_SUPPLIER_PRESENT", "NO_LINKED_SUPPLIER"]
SourcingReadinessOutcome = Literal[
    "READY_FOR_SOURCING_REVIEW",
    "SUPPLIER_LINK_REVIEW_REQUIRED",
]


class SourcingReviewScopeError(RuntimeError):
    """Raised when an ERPNext document is outside the event workflow scope."""


class AutoReorderReviewRequest(BaseModel):
    """Stable internal intent derived from an authenticated ERPNext event."""

    model_config = ConfigDict(extra="forbid")

    review_id: UUID
    material_request_name: str = Field(min_length=1, max_length=140)


class ItemSupplierReadiness(BaseModel):
    """Deterministic supplier-link result for one Material Request row."""

    row_name: str
    item_code: str
    warehouse: str
    requested_stock_qty: Decimal = Field(gt=0)
    stock_uom: str
    linked_suppliers: list[str]
    outcome: ItemReadinessOutcome


class AutoReorderSourcingAssessment(BaseModel):
    """Read-only value-added assessment of one native reorder request."""

    material_request_name: str
    material_request_modified: str
    company: str
    transaction_date: date
    schedule_date: date
    rule_version: Literal["supplier-link-v1"] = "supplier-link-v1"
    outcome: SourcingReadinessOutcome
    explanation: str
    items: list[ItemSupplierReadiness] = Field(min_length=1)


class AutoReorderReviewResponse(BaseModel):
    """Assessment bound to its stable review and authenticated source."""

    review_id: UUID
    source_id: str
    assessment: AutoReorderSourcingAssessment


def review_id_for_material_request(material_request_name: str) -> UUID:
    """Derive an idempotent review ID from the immutable ERPNext document name."""

    return uuid5(
        AUTO_REORDER_REVIEW_NAMESPACE,
        f"erpnext:auto-reorder:{material_request_name}",
    )


def assess_supplier_readiness(
    material_request: MaterialRequestRecord,
    items_by_code: dict[str, SourcingItemRecord],
) -> AutoReorderSourcingAssessment:
    """Evaluate Supplier links without selecting or ranking a Supplier."""

    item_results: list[ItemSupplierReadiness] = []
    for row in sorted(material_request.items, key=lambda candidate: candidate.idx):
        if not row.warehouse:
            raise ERPNextDataError("Material Request Item has no target Warehouse")
        item = items_by_code.get(row.item_code)
        if item is None:
            raise ERPNextDataError("Material Request Item was not read from ERPNext")
        if item.stock_uom != row.stock_uom:
            raise ERPNextDataError(
                "Material Request Item stock UOM does not match the Item master"
            )

        suppliers = sorted({link.supplier for link in item.supplier_items})
        item_results.append(
            ItemSupplierReadiness(
                row_name=row.name,
                item_code=row.item_code,
                warehouse=row.warehouse,
                requested_stock_qty=row.stock_qty,
                stock_uom=row.stock_uom,
                linked_suppliers=suppliers,
                outcome=(
                    "LINKED_SUPPLIER_PRESENT"
                    if suppliers
                    else "NO_LINKED_SUPPLIER"
                ),
            )
        )

    missing_count = sum(
        result.outcome == "NO_LINKED_SUPPLIER" for result in item_results
    )
    if missing_count:
        outcome: SourcingReadinessOutcome = "SUPPLIER_LINK_REVIEW_REQUIRED"
        explanation = (
            f"{missing_count} of {len(item_results)} Item rows have no Supplier "
            "linked in the ERPNext Item master."
        )
    else:
        outcome = "READY_FOR_SOURCING_REVIEW"
        explanation = (
            "Every Item row has at least one Supplier link; this does not rank, "
            "approve, or select a Supplier."
        )

    return AutoReorderSourcingAssessment(
        material_request_name=material_request.name,
        material_request_modified=material_request.modified,
        company=material_request.company,
        transaction_date=material_request.transaction_date,
        schedule_date=material_request.schedule_date,
        outcome=outcome,
        explanation=explanation,
        items=item_results,
    )


async def build_auto_reorder_sourcing_review(
    settings: Settings,
    request: AutoReorderReviewRequest,
    *,
    source_id: str,
) -> AutoReorderReviewResponse:
    """Re-read a native reorder request and build a no-write sourcing review."""

    async with ERPNextClient(settings) as client:
        logged_user = await client.get_logged_user()
        if logged_user != settings.erpnext_expected_user:
            raise ERPNextDataError("ERPNext returned an unexpected API identity")

        material_request = await client.get_material_request(
            request.material_request_name
        )
        if material_request.company != settings.erpnext_company:
            raise SourcingReviewScopeError(
                "Material Request belongs to an unexpected Company"
            )
        if material_request.material_request_type != "Purchase":
            raise SourcingReviewScopeError(
                "Material Request is not a Purchase request"
            )
        if material_request.docstatus != 1:
            raise SourcingReviewScopeError("Material Request is not submitted")
        if not material_request.auto_created_via_reorder:
            raise SourcingReviewScopeError(
                "Material Request was not created by ERPNext automatic reorder"
            )

        items_by_code: dict[str, SourcingItemRecord] = {}
        checked_warehouses: set[str] = set()
        for row in material_request.items:
            if not row.warehouse:
                raise ERPNextDataError(
                    "Material Request Item has no target Warehouse"
                )
            if row.warehouse not in checked_warehouses:
                await client.get_warehouse(
                    row.warehouse,
                    expected_company=material_request.company,
                )
                checked_warehouses.add(row.warehouse)
            if row.item_code not in items_by_code:
                items_by_code[row.item_code] = await client.get_sourcing_item(
                    row.item_code
                )

    assessment = assess_supplier_readiness(material_request, items_by_code)
    return AutoReorderReviewResponse(
        review_id=request.review_id,
        source_id=source_id,
        assessment=assessment,
    )
