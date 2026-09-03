import asyncio

from erpnext_agentic.config import get_settings
from erpnext_agentic.erpnext_client import (
    CompanyRecord,
    ERPNextClient,
    StockPosition,
    ValidatedItemReorder,
    WarehouseRecord,
)
from erpnext_agentic.replenishment import (
    ReplenishmentInputs,
    ReplenishmentProposal,
    calculate_replenishment,
)


async def check_connection() -> tuple[
    str,
    CompanyRecord,
    WarehouseRecord,
    ValidatedItemReorder,
    StockPosition,
    ReplenishmentProposal,
]:
    """Verify the ERPNext actor and first-milestone read configuration."""

    settings = get_settings()
    async with ERPNextClient(settings) as client:
        logged_user = await client.get_logged_user()

        if logged_user != settings.erpnext_expected_user:
            raise RuntimeError(
                "ERPNext returned an unexpected API identity; refusing to continue"
            )

        company = await client.get_company(settings.erpnext_company)
        warehouse = await client.get_warehouse(
            settings.erpnext_warehouse,
            expected_company=company.name,
        )
        item_reorder = await client.get_item_reorder(
            settings.erpnext_item,
            warehouse=warehouse.name,
        )
        stock_position = await client.get_stock_position(
            item_reorder.item.name,
            warehouse=warehouse.name,
        )

    proposal = calculate_replenishment(
        ReplenishmentInputs(
            item_code=item_reorder.item.name,
            warehouse=warehouse.name,
            stock_uom=item_reorder.item.stock_uom,
            projected_qty=stock_position.projected_qty,
            reorder_level=item_reorder.reorder_rule.warehouse_reorder_level,
            reorder_qty=item_reorder.reorder_rule.warehouse_reorder_qty,
        )
    )

    return logged_user, company, warehouse, item_reorder, stock_position, proposal


def main() -> None:
    (
        logged_user,
        company,
        warehouse,
        item_reorder,
        stock_position,
        proposal,
    ) = asyncio.run(check_connection())
    print(f"ERPNext authentication succeeded for: {logged_user}")
    print(f"ERPNext Company read succeeded: {company.name} [{company.abbr}]")
    print(
        "ERPNext Warehouse read succeeded: "
        f"{warehouse.name} [company={warehouse.company}, "
        f"group={warehouse.is_group}, disabled={warehouse.disabled}]"
    )
    print(
        "ERPNext Item/reorder read succeeded: "
        f"{item_reorder.item.name} [uom={item_reorder.item.stock_uom}, "
        f"lead_time_days={item_reorder.item.lead_time_days}, "
        f"reorder_level={item_reorder.reorder_rule.warehouse_reorder_level}, "
        f"reorder_qty={item_reorder.reorder_rule.warehouse_reorder_qty}, "
        f"type={item_reorder.reorder_rule.material_request_type}]"
    )
    bin_state = "present" if stock_position.bin_record else "missing"
    print(
        "ERPNext Bin read succeeded: "
        f"{bin_state} [projected_qty={stock_position.projected_qty}]"
    )
    print(
        "Replenishment calculation succeeded: "
        f"{proposal.outcome} [deficiency={proposal.deficiency}, "
        f"proposed_stock_qty={proposal.proposed_stock_qty} "
        f"{proposal.stock_uom}]"
    )


if __name__ == "__main__":
    main()
