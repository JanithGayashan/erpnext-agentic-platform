from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from erpnext_agentic.config import Settings
from erpnext_agentic.erpnext_client import (
    ERPNextDataError,
    ItemSupplierLink,
    MaterialRequestItemRecord,
    MaterialRequestRecord,
    SourcingItemRecord,
)
from erpnext_agentic.sourcing_review import (
    AutoReorderReviewRequest,
    SourcingReviewScopeError,
    assess_supplier_readiness,
    build_auto_reorder_sourcing_review,
)


def material_request(
    *,
    stock_uom: str = "Nos",
    company: str = "JG Investors (Demo)",
    request_type: str = "Purchase",
    docstatus: int = 1,
    auto_created_via_reorder: bool = True,
) -> MaterialRequestRecord:
    return MaterialRequestRecord(
        name="MAT-MR-2026-00003",
        title="Purchase",
        modified="2026-09-04 09:00:00.000000",
        docstatus=docstatus,
        status="Pending",
        material_request_type=request_type,
        company=company,
        transaction_date=date(2026, 9, 4),
        schedule_date=date(2026, 9, 7),
        auto_created_via_reorder=auto_created_via_reorder,
        items=[
            MaterialRequestItemRecord(
                name="MR-ITEM-00003",
                idx=1,
                item_code="AGENTIC-TEST-ITEM",
                item_name="Agentic Test Item",
                item_group="Agentic Test Items",
                qty=Decimal("20"),
                stock_qty=Decimal("20"),
                uom="Nos",
                stock_uom=stock_uom,
                conversion_factor=Decimal("1"),
                warehouse="Agentic Test Warehouse - JGD",
                schedule_date=date(2026, 9, 7),
                ordered_qty=Decimal("0"),
                received_qty=Decimal("0"),
            )
        ],
    )


def sourcing_item(*, suppliers: list[str]) -> SourcingItemRecord:
    return SourcingItemRecord(
        name="AGENTIC-TEST-ITEM",
        item_name="Agentic Test Item",
        item_group="Agentic Test Items",
        stock_uom="Nos",
        disabled=False,
        is_stock_item=True,
        has_variants=False,
        supplier_items=[
            ItemSupplierLink(supplier=supplier) for supplier in suppliers
        ],
    )


def test_missing_supplier_link_requires_review() -> None:
    result = assess_supplier_readiness(
        material_request(),
        {"AGENTIC-TEST-ITEM": sourcing_item(suppliers=[])},
    )

    assert result.outcome == "SUPPLIER_LINK_REVIEW_REQUIRED"
    assert result.items[0].outcome == "NO_LINKED_SUPPLIER"
    assert result.items[0].requested_stock_qty == Decimal("20")


def test_linked_suppliers_are_deduplicated_and_sorted() -> None:
    result = assess_supplier_readiness(
        material_request(),
        {
            "AGENTIC-TEST-ITEM": sourcing_item(
                suppliers=["Supplier B", "Supplier A", "Supplier A"]
            )
        },
    )

    assert result.outcome == "READY_FOR_SOURCING_REVIEW"
    assert result.items[0].outcome == "LINKED_SUPPLIER_PRESENT"
    assert result.items[0].linked_suppliers == ["Supplier A", "Supplier B"]


def test_missing_item_snapshot_fails_closed() -> None:
    with pytest.raises(ERPNextDataError, match="was not read"):
        assess_supplier_readiness(material_request(), {})


def test_stock_uom_mismatch_fails_closed() -> None:
    with pytest.raises(ERPNextDataError, match="stock UOM"):
        assess_supplier_readiness(
            material_request(stock_uom="Kg"),
            {"AGENTIC-TEST-ITEM": sourcing_item(suppliers=[])},
        )


class FakeERPNextClient:
    material_request_record = material_request()
    sourcing_item_record = sourcing_item(suppliers=[])

    def __init__(self, _settings: Settings) -> None:
        self.warehouse_reads: list[str] = []

    async def __aenter__(self) -> "FakeERPNextClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get_logged_user(self) -> str:
        return "agentic-api@jg-investors.test"

    async def get_material_request(self, _name: str) -> MaterialRequestRecord:
        return self.material_request_record

    async def get_warehouse(
        self,
        warehouse: str,
        *,
        expected_company: str,
    ) -> object:
        assert expected_company == "JG Investors (Demo)"
        self.warehouse_reads.append(warehouse)
        return object()

    async def get_sourcing_item(self, _item_code: str) -> SourcingItemRecord:
        return self.sourcing_item_record


def review_settings() -> Settings:
    return Settings(
        environment="test",
        erpnext_api_key="erpnext-key",
        erpnext_api_secret="erpnext-secret",
    )


@pytest.mark.anyio
async def test_review_builder_re_reads_trusted_erpnext_records(monkeypatch) -> None:
    monkeypatch.setattr(
        "erpnext_agentic.sourcing_review.ERPNextClient",
        FakeERPNextClient,
    )
    request = AutoReorderReviewRequest(
        review_id=UUID("f2756d51-f49e-5d4f-a63e-30b18d7fb886"),
        material_request_name="MAT-MR-2026-00003",
    )

    result = await build_auto_reorder_sourcing_review(
        review_settings(),
        request,
        source_id="erpnext-auto-reorder-webhook",
    )

    assert result.review_id == request.review_id
    assert result.assessment.material_request_name == request.material_request_name
    assert result.assessment.outcome == "SUPPLIER_LINK_REVIEW_REQUIRED"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("record", "expected_message"),
    [
        (material_request(company="Other Company"), "unexpected Company"),
        (material_request(request_type="Material Transfer"), "not a Purchase"),
        (material_request(docstatus=0), "not submitted"),
        (
            material_request(auto_created_via_reorder=False),
            "not created by ERPNext automatic reorder",
        ),
    ],
)
async def test_review_builder_rejects_wrong_document_scope(
    monkeypatch,
    record: MaterialRequestRecord,
    expected_message: str,
) -> None:
    class ScopedFakeClient(FakeERPNextClient):
        material_request_record = record

    monkeypatch.setattr(
        "erpnext_agentic.sourcing_review.ERPNextClient",
        ScopedFakeClient,
    )
    request = AutoReorderReviewRequest(
        review_id=UUID("f2756d51-f49e-5d4f-a63e-30b18d7fb886"),
        material_request_name="MAT-MR-2026-00003",
    )

    with pytest.raises(SourcingReviewScopeError, match=expected_message):
        await build_auto_reorder_sourcing_review(
            review_settings(),
            request,
            source_id="erpnext-auto-reorder-webhook",
        )
