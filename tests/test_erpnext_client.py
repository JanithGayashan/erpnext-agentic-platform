import asyncio
from datetime import date
from decimal import Decimal

import httpx
import pytest

from erpnext_agentic.config import Settings
from erpnext_agentic.erpnext_client import (
    ERPNextClient,
    ERPNextConfigurationError,
    ERPNextDataError,
)


def configured_settings() -> Settings:
    return Settings(
        environment="test",
        erpnext_base_url="http://erpnext.test:8000",
        erpnext_site_host="development.localhost",
        erpnext_api_key="fake-key",
        erpnext_api_secret="fake-secret",
    )


def test_get_logged_user_uses_authenticated_site_request() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/method/frappe.auth.get_logged_user"
        assert request.headers["host"] == "development.localhost"
        assert request.headers["authorization"] == "token fake-key:fake-secret"
        return httpx.Response(
            200,
            json={"message": "agentic-api@jg-investors.test"},
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            assert await client.get_logged_user() == "agentic-api@jg-investors.test"

    asyncio.run(run_test())


def test_get_time_zone_uses_supported_frappe_method() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/method/frappe.client.get_time_zone"
        return httpx.Response(
            200,
            json={"message": {"time_zone": "Asia/Colombo"}},
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            assert await client.get_time_zone() == "Asia/Colombo"

    asyncio.run(run_test())


def test_client_refuses_missing_credentials() -> None:
    settings = Settings(
        environment="test",
        erpnext_api_key=None,
        erpnext_api_secret=None,
    )

    with pytest.raises(ERPNextConfigurationError):
        ERPNextClient(settings)


def test_get_company_requests_only_required_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/resource/Company"
        assert request.url.params["fields"] == '["name", "company_name", "abbr"]'
        assert request.url.params["filters"] == (
            '[["name", "=", "JG Investors (Demo)"]]'
        )
        assert request.url.params["limit_page_length"] == "2"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "name": "JG Investors (Demo)",
                        "company_name": "JG Investors (Demo)",
                        "abbr": "JGD",
                    }
                ]
            },
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            company = await client.get_company("JG Investors (Demo)")
            assert company.abbr == "JGD"

    asyncio.run(run_test())


def test_get_company_fails_when_company_is_missing() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            with pytest.raises(ERPNextDataError):
                await client.get_company("Unknown Company")

    asyncio.run(run_test())


def test_get_warehouse_requests_and_validates_required_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/resource/Warehouse"
        assert request.url.params["fields"] == (
            '["name", "warehouse_name", "company", "parent_warehouse", '
            '"is_group", "disabled"]'
        )
        assert request.url.params["filters"] == (
            '[["name", "=", "Agentic Test Warehouse - JGD"]]'
        )
        assert request.url.params["limit_page_length"] == "2"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "name": "Agentic Test Warehouse - JGD",
                        "warehouse_name": "Agentic Test Warehouse",
                        "company": "JG Investors (Demo)",
                        "parent_warehouse": "All Warehouses - JGD",
                        "is_group": 0,
                        "disabled": 0,
                    }
                ]
            },
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            warehouse = await client.get_warehouse(
                "Agentic Test Warehouse - JGD",
                expected_company="JG Investors (Demo)",
            )
            assert warehouse.parent_warehouse == "All Warehouses - JGD"
            assert warehouse.is_group is False
            assert warehouse.disabled is False

    asyncio.run(run_test())


@pytest.mark.parametrize(
    ("company", "is_group", "disabled", "expected_message"),
    [
        ("Another Company", 0, 0, "unexpected Company"),
        ("JG Investors (Demo)", 1, 0, "group Warehouse"),
        ("JG Investors (Demo)", 0, 1, "disabled"),
    ],
)
def test_get_warehouse_rejects_ineligible_warehouse(
    company: str,
    is_group: int,
    disabled: int,
    expected_message: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "name": "Agentic Test Warehouse - JGD",
                        "warehouse_name": "Agentic Test Warehouse",
                        "company": company,
                        "parent_warehouse": "All Warehouses - JGD",
                        "is_group": is_group,
                        "disabled": disabled,
                    }
                ]
            },
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            with pytest.raises(ERPNextDataError, match=expected_message):
                await client.get_warehouse(
                    "Agentic Test Warehouse - JGD",
                    expected_company="JG Investors (Demo)",
                )

    asyncio.run(run_test())


def item_response(
    *,
    disabled: int = 0,
    is_stock_item: int = 1,
    has_variants: int = 0,
    variant_of: str | None = None,
    reorder_levels: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    if reorder_levels is None:
        reorder_levels = [
            {
                "warehouse": "Agentic Test Warehouse - JGD",
                "warehouse_group": "Agentic Test Warehouse - JGD",
                "warehouse_reorder_level": 10,
                "warehouse_reorder_qty": 20,
                "material_request_type": "Purchase",
            }
        ]

    return {
        "data": {
            "name": "AGENTIC-TEST-ITEM",
            "item_name": "Agentic Test Item",
            "item_group": "Agentic Test Items",
            "stock_uom": "Nos",
            "disabled": disabled,
            "is_stock_item": is_stock_item,
            "has_variants": has_variants,
            "variant_of": variant_of,
            "lead_time_days": 3,
            "reorder_levels": reorder_levels,
            "ignored_erpnext_field": "not part of our typed contract",
        }
    }


def test_item_model_accepts_omitted_empty_variant_field() -> None:
    response_body = item_response()
    data = response_body["data"]
    assert isinstance(data, dict)
    data.pop("variant_of")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_body)

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            result = await client.get_item_reorder(
                "AGENTIC-TEST-ITEM",
                warehouse="Agentic Test Warehouse - JGD",
            )
            assert result.item.variant_of is None

    asyncio.run(run_test())


def test_get_item_reorder_validates_item_and_purchase_rule() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/resource/Item/AGENTIC-TEST-ITEM"
        return httpx.Response(200, json=item_response())

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            result = await client.get_item_reorder(
                "AGENTIC-TEST-ITEM",
                warehouse="Agentic Test Warehouse - JGD",
            )
            assert result.item.stock_uom == "Nos"
            assert result.item.lead_time_days == 3
            assert result.reorder_rule.warehouse_reorder_level == 10
            assert result.reorder_rule.warehouse_reorder_qty == 20
            assert result.reorder_rule.material_request_type == "Purchase"

    asyncio.run(run_test())


@pytest.mark.parametrize(
    ("item_overrides", "expected_message"),
    [
        ({"disabled": 1}, "disabled"),
        ({"is_stock_item": 0}, "not a stock Item"),
        ({"has_variants": 1}, "templates and variants"),
        ({"variant_of": "ITEM-TEMPLATE"}, "templates and variants"),
    ],
)
def test_get_item_reorder_rejects_ineligible_item(
    item_overrides: dict[str, object],
    expected_message: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=item_response(**item_overrides))

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            with pytest.raises(ERPNextDataError, match=expected_message):
                await client.get_item_reorder(
                    "AGENTIC-TEST-ITEM",
                    warehouse="Agentic Test Warehouse - JGD",
                )

    asyncio.run(run_test())


@pytest.mark.parametrize(
    ("reorder_levels", "expected_message"),
    [
        ([], "exactly one reorder rule"),
        (
            [
                {
                    "warehouse": "Agentic Test Warehouse - JGD",
                    "warehouse_group": "All Warehouses - JGD",
                    "warehouse_reorder_level": 10,
                    "warehouse_reorder_qty": 20,
                    "material_request_type": "Purchase",
                }
            ],
            "Group Warehouse aggregation",
        ),
        (
            [
                {
                    "warehouse": "Agentic Test Warehouse - JGD",
                    "warehouse_group": "Agentic Test Warehouse - JGD",
                    "warehouse_reorder_level": 10,
                    "warehouse_reorder_qty": 20,
                    "material_request_type": "Material Transfer",
                }
            ],
            "not for Purchase",
        ),
        (
            [
                {
                    "warehouse": "Agentic Test Warehouse - JGD",
                    "warehouse_group": "Agentic Test Warehouse - JGD",
                    "warehouse_reorder_level": 0,
                    "warehouse_reorder_qty": 0,
                    "material_request_type": "Purchase",
                }
            ],
            "both zero",
        ),
    ],
)
def test_get_item_reorder_rejects_unsupported_rule(
    reorder_levels: list[dict[str, object]],
    expected_message: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=item_response(reorder_levels=reorder_levels),
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            with pytest.raises(ERPNextDataError, match=expected_message):
                await client.get_item_reorder(
                    "AGENTIC-TEST-ITEM",
                    warehouse="Agentic Test Warehouse - JGD",
                )

    asyncio.run(run_test())


def test_get_stock_position_normalizes_missing_bin_to_zero() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/resource/Bin"
        assert request.url.params["fields"] == (
            '["name", "item_code", "warehouse", "actual_qty", "planned_qty", '
            '"indented_qty", "ordered_qty", "reserved_qty", "projected_qty"]'
        )
        assert request.url.params["filters"] == (
            '[["item_code", "=", "AGENTIC-TEST-ITEM"], '
            '["warehouse", "=", "Agentic Test Warehouse - JGD"]]'
        )
        assert request.url.params["limit_page_length"] == "2"
        return httpx.Response(200, json={"data": []})

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            position = await client.get_stock_position(
                "AGENTIC-TEST-ITEM",
                warehouse="Agentic Test Warehouse - JGD",
            )
            assert position.projected_qty == 0
            assert position.bin_record is None

    asyncio.run(run_test())


def test_get_stock_position_uses_erpnext_projected_quantity() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "name": "BIN-TEST",
                        "item_code": "AGENTIC-TEST-ITEM",
                        "warehouse": "Agentic Test Warehouse - JGD",
                        "actual_qty": 5,
                        "planned_qty": 0,
                        "indented_qty": 0,
                        "ordered_qty": 10,
                        "reserved_qty": 3,
                        "projected_qty": 12,
                    }
                ]
            },
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            position = await client.get_stock_position(
                "AGENTIC-TEST-ITEM",
                warehouse="Agentic Test Warehouse - JGD",
            )
            assert position.projected_qty == 12
            assert position.bin_record is not None
            assert position.bin_record.actual_qty == 5

    asyncio.run(run_test())


def test_get_stock_position_rejects_duplicate_bins() -> None:
    bin_record = {
        "name": "BIN-TEST",
        "item_code": "AGENTIC-TEST-ITEM",
        "warehouse": "Agentic Test Warehouse - JGD",
        "actual_qty": 0,
        "planned_qty": 0,
        "indented_qty": 0,
        "ordered_qty": 0,
        "reserved_qty": 0,
        "projected_qty": 0,
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [bin_record, bin_record]})

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            with pytest.raises(ERPNextDataError, match="multiple Bins"):
                await client.get_stock_position(
                    "AGENTIC-TEST-ITEM",
                    warehouse="Agentic Test Warehouse - JGD",
                )

    asyncio.run(run_test())


def test_get_stock_position_rejects_mismatched_bin() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "name": "BIN-OTHER",
                        "item_code": "OTHER-ITEM",
                        "warehouse": "Agentic Test Warehouse - JGD",
                        "actual_qty": 0,
                        "planned_qty": 0,
                        "indented_qty": 0,
                        "ordered_qty": 0,
                        "reserved_qty": 0,
                        "projected_qty": 0,
                    }
                ]
            },
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            with pytest.raises(ERPNextDataError, match="mismatched Bin"):
                await client.get_stock_position(
                    "AGENTIC-TEST-ITEM",
                    warehouse="Agentic Test Warehouse - JGD",
                )

    asyncio.run(run_test())


def test_get_uom_requests_and_validates_required_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/resource/UOM"
        assert request.url.params["fields"] == (
            '["name", "uom_name", "must_be_whole_number", "enabled"]'
        )
        assert request.url.params["filters"] == '[["name", "=", "Nos"]]'
        assert request.url.params["limit_page_length"] == "2"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "name": "Nos",
                        "uom_name": "Nos",
                        "must_be_whole_number": 1,
                        "enabled": 1,
                    }
                ]
            },
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            uom = await client.get_uom("Nos")
            assert uom.enabled is True
            assert uom.must_be_whole_number is True

    asyncio.run(run_test())


@pytest.mark.parametrize(
    ("records", "expected_message"),
    [
        ([], "exactly the requested UOM"),
        (
            [
                {
                    "name": "Nos",
                    "uom_name": "Nos",
                    "must_be_whole_number": 1,
                    "enabled": 0,
                }
            ],
            "disabled",
        ),
        (
            [
                {
                    "name": "Nos",
                    "uom_name": "Different",
                    "must_be_whole_number": 1,
                    "enabled": 1,
                }
            ],
            "mismatched UOM name",
        ),
    ],
)
def test_get_uom_rejects_invalid_result(
    records: list[dict[str, object]],
    expected_message: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": records})

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            with pytest.raises(ERPNextDataError, match=expected_message):
                await client.get_uom("Nos")

    asyncio.run(run_test())


def material_request_response(
    *,
    name: str = "MAT-MR-2026-00001",
) -> dict[str, object]:
    return {
        "data": {
            "name": name,
            "title": "Agentic Lead-Time Risk Test",
            "modified": "2026-09-03 14:10:00.000000",
            "docstatus": 0,
            "status": "Draft",
            "material_request_type": "Purchase",
            "company": "JG Investors (Demo)",
            "transaction_date": "2026-09-03",
            "schedule_date": "2026-09-04",
            "auto_created_via_reorder": 0,
            "items": [
                {
                    "name": "MR-ITEM-00001",
                    "idx": 1,
                    "item_code": "AGENTIC-TEST-ITEM",
                    "item_name": "Agentic Test Item",
                    "item_group": "Agentic Test Items",
                    "qty": 20,
                    "stock_qty": 20,
                    "uom": "Nos",
                    "stock_uom": "Nos",
                    "conversion_factor": 1,
                    "warehouse": "Agentic Test Warehouse - JGD",
                    "schedule_date": "2026-09-04",
                    "ordered_qty": 0,
                    "received_qty": 0,
                    "ignored_child_field": "not checkpointed",
                }
            ],
            "ignored_header_field": "not checkpointed",
        }
    }


def test_get_material_request_reads_exact_typed_resource() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == (
            "/api/resource/Material Request/MAT-MR-2026-00001"
        )
        return httpx.Response(200, json=material_request_response())

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            material_request = await client.get_material_request(
                "MAT-MR-2026-00001"
            )

        assert material_request.name == "MAT-MR-2026-00001"
        assert material_request.docstatus == 0
        assert material_request.transaction_date == date(2026, 9, 3)
        assert material_request.schedule_date == date(2026, 9, 4)
        assert len(material_request.items) == 1
        assert material_request.items[0].qty == Decimal("20")
        assert material_request.items[0].stock_uom == "Nos"
        assert not hasattr(material_request, "ignored_header_field")
        assert not hasattr(material_request.items[0], "ignored_child_field")

    asyncio.run(run_test())


def auto_reorder_material_request_response(
    **overrides: object,
) -> dict[str, object]:
    record: dict[str, object] = {
        "name": "MAT-MR-2026-00003",
        "modified": "2026-09-03 15:00:00.000000",
        "docstatus": 1,
        "status": "Pending",
        "material_request_type": "Purchase",
        "company": "JG Investors (Demo)",
        "transaction_date": "2026-09-03",
        "schedule_date": "2026-09-06",
        "auto_created_via_reorder": 1,
        "ignored_field": "not retained",
    }
    record.update(overrides)
    return {"data": [record]}


def test_get_recent_auto_reorder_material_requests_uses_safe_filters() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/resource/Material Request"
        assert request.url.params["fields"] == (
            '["name", "modified", "docstatus", "status", '
            '"material_request_type", "company", "transaction_date", '
            '"schedule_date", "auto_created_via_reorder"]'
        )
        assert request.url.params["filters"] == (
            '[["company", "=", "JG Investors (Demo)"], '
            '["material_request_type", "=", "Purchase"], '
            '["auto_created_via_reorder", "=", 1], ["docstatus", "=", 1]]'
        )
        assert request.url.params["order_by"] == "modified desc"
        assert request.url.params["limit_page_length"] == "10"
        return httpx.Response(200, json=auto_reorder_material_request_response())

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            records = await client.get_recent_auto_reorder_material_requests(
                company="JG Investors (Demo)",
                limit=10,
            )

        assert len(records) == 1
        assert records[0].name == "MAT-MR-2026-00003"
        assert records[0].docstatus == 1
        assert records[0].auto_created_via_reorder is True
        assert not hasattr(records[0], "ignored_field")

    asyncio.run(run_test())


def test_get_recent_auto_reorder_material_requests_accepts_empty_result() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            records = await client.get_recent_auto_reorder_material_requests(
                company="JG Investors (Demo)"
            )

        assert records == []

    asyncio.run(run_test())


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"company": "Other Company"}, "another Company"),
        ({"material_request_type": "Material Transfer"}, "non-Purchase"),
        ({"auto_created_via_reorder": 0}, "not created by automatic reorder"),
        ({"docstatus": 0}, "not submitted"),
    ],
)
def test_get_recent_auto_reorder_material_requests_revalidates_server_filters(
    overrides: dict[str, object],
    expected_message: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=auto_reorder_material_request_response(**overrides),
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            with pytest.raises(ERPNextDataError, match=expected_message):
                await client.get_recent_auto_reorder_material_requests(
                    company="JG Investors (Demo)"
                )

    asyncio.run(run_test())


@pytest.mark.parametrize("limit", [0, 101])
def test_get_recent_auto_reorder_material_requests_rejects_unsafe_limit(
    limit: int,
) -> None:
    async def run_test() -> None:
        async with ERPNextClient(configured_settings()) as client:
            with pytest.raises(ValueError, match="between 1 and 100"):
                await client.get_recent_auto_reorder_material_requests(
                    company="JG Investors (Demo)",
                    limit=limit,
                )

    asyncio.run(run_test())


def test_get_material_request_percent_encodes_document_name() -> None:
    requested_name = "MR/Test 01"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path == b"/api/resource/Material%20Request/MR%2FTest%2001"
        return httpx.Response(
            200,
            json=material_request_response(name=requested_name),
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            result = await client.get_material_request(requested_name)
            assert result.name == requested_name

    asyncio.run(run_test())


def test_get_material_request_rejects_mismatched_document() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=material_request_response(name="MAT-MR-2026-OTHER"),
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            with pytest.raises(ERPNextDataError, match="unexpected Material Request"):
                await client.get_material_request("MAT-MR-2026-00001")

    asyncio.run(run_test())


def sourcing_item_response(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "name": "AGENTIC-TEST-ITEM",
        "item_name": "Agentic Test Item",
        "item_group": "Agentic Test Items",
        "stock_uom": "Nos",
        "disabled": 0,
        "is_stock_item": 1,
        "has_variants": 0,
        "variant_of": None,
        "supplier_items": [
            {
                "supplier": "Agentic Test Supplier",
                "supplier_part_no": "SUP-PART-001",
            }
        ],
        "ignored_item_field": "not retained",
    }
    record.update(overrides)
    return {"data": record}


def test_get_sourcing_item_reads_typed_supplier_links() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/resource/Item/AGENTIC-TEST-ITEM"
        return httpx.Response(200, json=sourcing_item_response())

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            item = await client.get_sourcing_item("AGENTIC-TEST-ITEM")

        assert item.name == "AGENTIC-TEST-ITEM"
        assert item.supplier_items[0].supplier == "Agentic Test Supplier"
        assert item.supplier_items[0].supplier_part_no == "SUP-PART-001"
        assert not hasattr(item, "ignored_item_field")

    asyncio.run(run_test())


def test_get_sourcing_item_accepts_no_supplier_links() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=sourcing_item_response(supplier_items=[]),
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            item = await client.get_sourcing_item("AGENTIC-TEST-ITEM")

        assert item.supplier_items == []

    asyncio.run(run_test())


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"name": "OTHER-ITEM"}, "unexpected sourcing Item"),
        ({"disabled": 1}, "disabled"),
        ({"is_stock_item": 0}, "not a stock Item"),
        ({"has_variants": 1}, "templates or variants"),
        ({"variant_of": "ITEM-TEMPLATE"}, "templates or variants"),
    ],
)
def test_get_sourcing_item_rejects_ineligible_item(
    overrides: dict[str, object],
    expected_message: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=sourcing_item_response(**overrides))

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with ERPNextClient(configured_settings(), transport=transport) as client:
            with pytest.raises(ERPNextDataError, match=expected_message):
                await client.get_sourcing_item("AGENTIC-TEST-ITEM")

    asyncio.run(run_test())
