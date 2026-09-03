from decimal import Decimal

import pytest
from pydantic import ValidationError

from erpnext_agentic.replenishment import (
    ReplenishmentInputs,
    calculate_replenishment,
)


def inputs(
    *,
    projected_qty: str,
    reorder_level: str = "10",
    reorder_qty: str = "20",
) -> ReplenishmentInputs:
    return ReplenishmentInputs(
        item_code="AGENTIC-TEST-ITEM",
        warehouse="Agentic Test Warehouse - JGD",
        stock_uom="Nos",
        projected_qty=Decimal(projected_qty),
        reorder_level=Decimal(reorder_level),
        reorder_qty=Decimal(reorder_qty),
    )


def test_missing_bin_scenario_proposes_configured_quantity() -> None:
    proposal = calculate_replenishment(inputs(projected_qty="0"))

    assert proposal.outcome == "PROPOSAL"
    assert proposal.deficiency == 10
    assert proposal.proposed_stock_qty == 20


def test_quantity_equal_to_reorder_level_triggers_proposal() -> None:
    proposal = calculate_replenishment(inputs(projected_qty="10"))

    assert proposal.outcome == "PROPOSAL"
    assert proposal.deficiency == 0
    assert proposal.proposed_stock_qty == 20


def test_quantity_above_reorder_level_returns_no_action() -> None:
    proposal = calculate_replenishment(inputs(projected_qty="11"))

    assert proposal.outcome == "NO_ACTION"
    assert proposal.deficiency == 0
    assert proposal.proposed_stock_qty == 0


def test_deficiency_can_exceed_configured_reorder_quantity() -> None:
    proposal = calculate_replenishment(inputs(projected_qty="-15"))

    assert proposal.outcome == "PROPOSAL"
    assert proposal.deficiency == 25
    assert proposal.proposed_stock_qty == 25


def test_deficiency_is_used_when_configured_quantity_is_zero() -> None:
    proposal = calculate_replenishment(
        inputs(projected_qty="5", reorder_qty="0")
    )

    assert proposal.outcome == "PROPOSAL"
    assert proposal.deficiency == 5
    assert proposal.proposed_stock_qty == 5


def test_both_reorder_values_cannot_be_zero() -> None:
    with pytest.raises(ValidationError, match="cannot both be zero"):
        inputs(projected_qty="0", reorder_level="0", reorder_qty="0")


@pytest.mark.parametrize("field", ["item_code", "warehouse", "stock_uom"])
def test_identity_fields_cannot_be_empty(field: str) -> None:
    values = {
        "item_code": "AGENTIC-TEST-ITEM",
        "warehouse": "Agentic Test Warehouse - JGD",
        "stock_uom": "Nos",
        "projected_qty": Decimal("0"),
        "reorder_level": Decimal("10"),
        "reorder_qty": Decimal("20"),
    }
    values[field] = ""

    with pytest.raises(ValidationError):
        ReplenishmentInputs(**values)

