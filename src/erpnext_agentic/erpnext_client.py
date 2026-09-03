import json
from decimal import Decimal
from types import TracebackType
from typing import Self
from urllib.parse import quote

import httpx
from pydantic import BaseModel

from erpnext_agentic.config import Settings


class ERPNextConfigurationError(RuntimeError):
    """Raised when required ERPNext client configuration is missing."""


class ERPNextDataError(RuntimeError):
    """Raised when ERPNext data does not match the required contract."""


class LoggedUserResponse(BaseModel):
    """Validated response from Frappe's logged-user method."""

    message: str


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
