import json
from datetime import date
from decimal import Decimal
from types import TracebackType
from typing import Literal, Self
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from erpnext_agentic.config import Settings


class ERPNextConfigurationError(RuntimeError):
    """Raised when required ERPNext client configuration is missing."""


class ERPNextDataError(RuntimeError):
    """Raised when ERPNext data does not match the required contract."""


class LoggedUserResponse(BaseModel):
    """Validated response from Frappe's logged-user method."""

    message: str


class TimeZoneDetails(BaseModel):
    """Site timezone returned by Frappe."""

    time_zone: str


class TimeZoneResponse(BaseModel):
    """Validated response from Frappe's timezone method."""

    message: TimeZoneDetails


class CompanyRecord(BaseModel):
    """Company fields required by the first milestone."""

    name: str
    company_name: str
    abbr: str


class CompanyListResponse(BaseModel):
    """Validated list response from the Company resource endpoint."""

    data: list[CompanyRecord]


class WarehouseRecord(BaseModel):
    """Validated Warehouse fields required by the first milestone."""

    name: str
    warehouse_name: str
    company: str
    parent_warehouse: str | None
    is_group: bool
    disabled: bool


class WarehouseListResponse(BaseModel):
    """Validated list response from the Warehouse resource endpoint."""

    data: list[WarehouseRecord]


class ItemReorderRecord(BaseModel):
    """One Item Reorder child row returned by ERPNext."""

    warehouse: str | None = None
    warehouse_group: str | None = None
    warehouse_reorder_level: Decimal
    warehouse_reorder_qty: Decimal
    material_request_type: str


class ItemRecord(BaseModel):
    """Item and reorder fields required by the first milestone."""

    name: str
    item_name: str
    item_group: str
    stock_uom: str
    disabled: bool
    is_stock_item: bool
    has_variants: bool
    variant_of: str | None = None
    lead_time_days: int
    reorder_levels: list[ItemReorderRecord]


class ItemResourceResponse(BaseModel):
    """Validated response from one Item resource endpoint."""

    data: ItemRecord


class ValidatedItemReorder(BaseModel):
    """An eligible Item paired with its one applicable reorder rule."""

    item: ItemRecord
    reorder_rule: ItemReorderRecord


class UOMRecord(BaseModel):
    """Unit-of-measure rules required by the first milestone."""

    name: str
    uom_name: str
    must_be_whole_number: bool
    enabled: bool


class UOMListResponse(BaseModel):
    """Validated list response from the UOM resource endpoint."""

    data: list[UOMRecord]


class BinRecord(BaseModel):
    """ERPNext stock summary for one Item and Warehouse."""

    name: str
    item_code: str
    warehouse: str
    actual_qty: Decimal
    planned_qty: Decimal
    indented_qty: Decimal
    ordered_qty: Decimal
    reserved_qty: Decimal
    projected_qty: Decimal


class BinListResponse(BaseModel):
    """Validated list response from the Bin resource endpoint."""

    data: list[BinRecord]


class StockPosition(BaseModel):
    """Normalized stock position used by replenishment calculations."""

    item_code: str
    warehouse: str
    projected_qty: Decimal
    bin_record: BinRecord | None


class MaterialRequestItemRecord(BaseModel):
    """Typed fields read from one Material Request Item row."""

    name: str = Field(min_length=1)
    idx: int = Field(ge=1)
    item_code: str = Field(min_length=1)
    item_name: str = Field(min_length=1)
    item_group: str = Field(min_length=1)
    qty: Decimal = Field(gt=0)
    stock_qty: Decimal = Field(gt=0)
    uom: str = Field(min_length=1)
    stock_uom: str = Field(min_length=1)
    conversion_factor: Decimal = Field(gt=0)
    warehouse: str | None = None
    schedule_date: date
    ordered_qty: Decimal = Field(ge=0)
    received_qty: Decimal = Field(ge=0)


class MaterialRequestRecord(BaseModel):
    """Typed read-only Material Request snapshot used by later policy code."""

    name: str = Field(min_length=1)
    title: str = Field(min_length=1)
    modified: str = Field(min_length=1)
    docstatus: Literal[0, 1, 2]
    status: str = Field(min_length=1)
    material_request_type: str = Field(min_length=1)
    company: str = Field(min_length=1)
    transaction_date: date
    schedule_date: date
    auto_created_via_reorder: bool
    items: list[MaterialRequestItemRecord] = Field(min_length=1)


class MaterialRequestResourceResponse(BaseModel):
    """Validated response from one Material Request resource endpoint."""

    data: MaterialRequestRecord


class AutoReorderMaterialRequestRecord(BaseModel):
    """Small discovery record for a submitted ERPNext auto-reorder request."""

    name: str = Field(min_length=1)
    modified: str = Field(min_length=1)
    docstatus: Literal[0, 1, 2]
    status: str = Field(min_length=1)
    material_request_type: str = Field(min_length=1)
    company: str = Field(min_length=1)
    transaction_date: date
    schedule_date: date
    auto_created_via_reorder: bool


class AutoReorderMaterialRequestListResponse(BaseModel):
    """Validated list response for ERPNext-created reorder requests."""

    data: list[AutoReorderMaterialRequestRecord]


class ItemSupplierLink(BaseModel):
    """One Supplier linked from an ERPNext Item master."""

    supplier: str = Field(min_length=1)
    supplier_part_no: str | None = None


class SourcingItemRecord(BaseModel):
    """Typed Item fields required by the supplier-readiness policy."""

    name: str = Field(min_length=1)
    item_name: str = Field(min_length=1)
    item_group: str = Field(min_length=1)
    stock_uom: str = Field(min_length=1)
    disabled: bool
    is_stock_item: bool
    has_variants: bool
    variant_of: str | None = None
    supplier_items: list[ItemSupplierLink] = Field(default_factory=list)


class SourcingItemResourceResponse(BaseModel):
    """Validated response from one Item resource used for sourcing review."""

    data: SourcingItemRecord


class ERPNextClient:
    """Small authenticated client for explicitly allowed ERPNext API calls."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if settings.erpnext_api_key is None or not settings.erpnext_api_key.get_secret_value():
            raise ERPNextConfigurationError("ERPNext API key is not configured")
        if settings.erpnext_api_secret is None or not settings.erpnext_api_secret.get_secret_value():
            raise ERPNextConfigurationError("ERPNext API secret is not configured")

        token = (
            f"{settings.erpnext_api_key.get_secret_value()}:"
            f"{settings.erpnext_api_secret.get_secret_value()}"
        )
        self._client = httpx.AsyncClient(
            base_url=str(settings.erpnext_base_url),
            headers={
                "Accept": "application/json",
                "Authorization": f"token {token}",
                "Host": settings.erpnext_site_host,
            },
            timeout=httpx.Timeout(10.0),
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_logged_user(self) -> str:
        """Return the ERPNext identity associated with the configured token."""

        response = await self._client.get("api/method/frappe.auth.get_logged_user")
        response.raise_for_status()
        return LoggedUserResponse.model_validate_json(response.content).message

    async def get_time_zone(self) -> str:
        """Return the ERPNext site's configured IANA timezone name."""

        response = await self._client.get("api/method/frappe.client.get_time_zone")
        response.raise_for_status()
        return TimeZoneResponse.model_validate_json(response.content).message.time_zone

    async def get_company(self, company: str) -> CompanyRecord:
        """Read one exact Company using an explicit field allowlist."""

        response = await self._client.get(
            "api/resource/Company",
            params={
                "fields": json.dumps(["name", "company_name", "abbr"]),
                "filters": json.dumps([["name", "=", company]]),
                "limit_page_length": "2",
            },
        )
        response.raise_for_status()
        records = CompanyListResponse.model_validate_json(response.content).data

        if len(records) != 1 or records[0].name != company:
            raise ERPNextDataError("ERPNext did not return exactly the requested Company")

        return records[0]

    async def get_warehouse(
        self,
        warehouse: str,
        *,
        expected_company: str,
    ) -> WarehouseRecord:
        """Read and validate one enabled, non-group Warehouse."""

        response = await self._client.get(
            "api/resource/Warehouse",
            params={
                "fields": json.dumps(
                    [
                        "name",
                        "warehouse_name",
                        "company",
                        "parent_warehouse",
                        "is_group",
                        "disabled",
                    ]
                ),
                "filters": json.dumps([["name", "=", warehouse]]),
                "limit_page_length": "2",
            },
        )
        response.raise_for_status()
        records = WarehouseListResponse.model_validate_json(response.content).data

        if len(records) != 1 or records[0].name != warehouse:
            raise ERPNextDataError(
                "ERPNext did not return exactly the requested Warehouse"
            )

        record = records[0]
        if record.company != expected_company:
            raise ERPNextDataError("Warehouse belongs to an unexpected Company")
        if record.disabled:
            raise ERPNextDataError("Warehouse is disabled")
        if record.is_group:
            raise ERPNextDataError("Warehouse is a group Warehouse")

        return record

    async def get_item_reorder(
        self,
        item_code: str,
        *,
        warehouse: str,
    ) -> ValidatedItemReorder:
        """Read an Item and validate its one Purchase reorder rule."""

        encoded_item_code = quote(item_code, safe="")
        response = await self._client.get(f"api/resource/Item/{encoded_item_code}")
        response.raise_for_status()
        item = ItemResourceResponse.model_validate_json(response.content).data

        if item.name != item_code:
            raise ERPNextDataError("ERPNext returned an unexpected Item")
        if item.disabled:
            raise ERPNextDataError("Item is disabled")
        if not item.is_stock_item:
            raise ERPNextDataError("Item is not a stock Item")
        if item.has_variants or item.variant_of:
            raise ERPNextDataError("Item templates and variants are not supported")
        if not item.stock_uom:
            raise ERPNextDataError("Item has no stock UOM")
        if item.lead_time_days < 0:
            raise ERPNextDataError("Item lead time cannot be negative")

        matching_rules = [
            rule for rule in item.reorder_levels if rule.warehouse == warehouse
        ]
        if len(matching_rules) != 1:
            raise ERPNextDataError(
                "Item does not have exactly one reorder rule for the Warehouse"
            )

        rule = matching_rules[0]
        if rule.warehouse_group not in (None, "", warehouse):
            raise ERPNextDataError("Group Warehouse aggregation is not supported")
        if rule.material_request_type != "Purchase":
            raise ERPNextDataError("Item reorder rule is not for Purchase")
        if rule.warehouse_reorder_level <= 0 and rule.warehouse_reorder_qty <= 0:
            raise ERPNextDataError("Item reorder level and quantity are both zero")

        return ValidatedItemReorder(item=item, reorder_rule=rule)

    async def get_stock_position(
        self,
        item_code: str,
        *,
        warehouse: str,
    ) -> StockPosition:
        """Read one Bin or normalize a missing Bin to projected quantity zero."""

        response = await self._client.get(
            "api/resource/Bin",
            params={
                "fields": json.dumps(
                    [
                        "name",
                        "item_code",
                        "warehouse",
                        "actual_qty",
                        "planned_qty",
                        "indented_qty",
                        "ordered_qty",
                        "reserved_qty",
                        "projected_qty",
                    ]
                ),
                "filters": json.dumps(
                    [
                        ["item_code", "=", item_code],
                        ["warehouse", "=", warehouse],
                    ]
                ),
                "limit_page_length": "2",
            },
        )
        response.raise_for_status()
        records = BinListResponse.model_validate_json(response.content).data

        if not records:
            return StockPosition(
                item_code=item_code,
                warehouse=warehouse,
                projected_qty=Decimal("0"),
                bin_record=None,
            )
        if len(records) != 1:
            raise ERPNextDataError(
                "ERPNext returned multiple Bins for one Item and Warehouse"
            )

        record = records[0]
        if record.item_code != item_code or record.warehouse != warehouse:
            raise ERPNextDataError("ERPNext returned a mismatched Bin")

        return StockPosition(
            item_code=item_code,
            warehouse=warehouse,
            projected_qty=record.projected_qty,
            bin_record=record,
        )

    async def get_uom(self, uom: str) -> UOMRecord:
        """Read and validate the Item's enabled stock UOM."""

        response = await self._client.get(
            "api/resource/UOM",
            params={
                "fields": json.dumps(
                    ["name", "uom_name", "must_be_whole_number", "enabled"]
                ),
                "filters": json.dumps([["name", "=", uom]]),
                "limit_page_length": "2",
            },
        )
        response.raise_for_status()
        records = UOMListResponse.model_validate_json(response.content).data

        if len(records) != 1 or records[0].name != uom:
            raise ERPNextDataError("ERPNext did not return exactly the requested UOM")

        record = records[0]
        if record.uom_name != uom:
            raise ERPNextDataError("ERPNext returned a mismatched UOM name")
        if not record.enabled:
            raise ERPNextDataError("UOM is disabled")

        return record

    async def get_material_request(self, name: str) -> MaterialRequestRecord:
        """Read one exact Material Request and reduce it to typed safe fields."""

        encoded_name = quote(name, safe="")
        response = await self._client.get(
            f"api/resource/Material%20Request/{encoded_name}"
        )
        response.raise_for_status()
        record = MaterialRequestResourceResponse.model_validate_json(
            response.content
        ).data

        if record.name != name:
            raise ERPNextDataError(
                "ERPNext returned an unexpected Material Request"
            )

        return record

    async def get_recent_auto_reorder_material_requests(
        self,
        *,
        company: str,
        limit: int = 20,
    ) -> list[AutoReorderMaterialRequestRecord]:
        """Discover recent submitted Purchase requests created by ERPNext reorder."""

        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        response = await self._client.get(
            "api/resource/Material%20Request",
            params={
                "fields": json.dumps(
                    [
                        "name",
                        "modified",
                        "docstatus",
                        "status",
                        "material_request_type",
                        "company",
                        "transaction_date",
                        "schedule_date",
                        "auto_created_via_reorder",
                    ]
                ),
                "filters": json.dumps(
                    [
                        ["company", "=", company],
                        ["material_request_type", "=", "Purchase"],
                        ["auto_created_via_reorder", "=", 1],
                        ["docstatus", "=", 1],
                    ]
                ),
                "order_by": "modified desc",
                "limit_page_length": str(limit),
            },
        )
        response.raise_for_status()
        records = AutoReorderMaterialRequestListResponse.model_validate_json(
            response.content
        ).data

        for record in records:
            if record.company != company:
                raise ERPNextDataError(
                    "ERPNext returned an auto-reorder request for another Company"
                )
            if record.material_request_type != "Purchase":
                raise ERPNextDataError(
                    "ERPNext returned a non-Purchase auto-reorder request"
                )
            if not record.auto_created_via_reorder:
                raise ERPNextDataError(
                    "ERPNext returned a request not created by automatic reorder"
                )
            if record.docstatus != 1:
                raise ERPNextDataError(
                    "ERPNext returned an auto-reorder request that is not submitted"
                )

        return records

    async def get_sourcing_item(self, item_code: str) -> SourcingItemRecord:
        """Read one Item and the Supplier links needed for sourcing review."""

        encoded_item_code = quote(item_code, safe="")
        response = await self._client.get(f"api/resource/Item/{encoded_item_code}")
        response.raise_for_status()
        record = SourcingItemResourceResponse.model_validate_json(
            response.content
        ).data

        if record.name != item_code:
            raise ERPNextDataError("ERPNext returned an unexpected sourcing Item")
        if record.disabled:
            raise ERPNextDataError("Sourcing Item is disabled")
        if not record.is_stock_item:
            raise ERPNextDataError("Sourcing Item is not a stock Item")
        if record.has_variants or record.variant_of:
            raise ERPNextDataError(
                "Sourcing review does not support Item templates or variants"
            )

        return record
