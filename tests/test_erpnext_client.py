import asyncio

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
