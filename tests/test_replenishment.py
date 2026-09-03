from datetime import datetime, timezone
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
        whole_number_required=True,
        lead_time_days=3,
        site_time_zone="Asia/Colombo",
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


def test_whole_number_uom_rounds_proposed_quantity_upward() -> None:
    proposal = calculate_replenishment(
        inputs(projected_qty="0", reorder_qty="20.2")
    )

    assert proposal.proposed_stock_qty == 21


def test_fractional_uom_preserves_decimal_proposed_quantity() -> None:
    calculation_inputs = inputs(projected_qty="0", reorder_qty="20.2")
    calculation_inputs.whole_number_required = False

    proposal = calculate_replenishment(calculation_inputs)

    assert proposal.proposed_stock_qty == Decimal("20.2")


def test_dates_use_site_timezone_and_item_lead_time() -> None:
    evaluated_at = datetime(2026, 9, 3, 20, 0, tzinfo=timezone.utc)

    proposal = calculate_replenishment(
        inputs(projected_qty="0"),
        evaluated_at=evaluated_at,
    )

    assert str(proposal.transaction_date) == "2026-09-04"
    assert str(proposal.schedule_date) == "2026-09-07"
    assert proposal.lead_time_days == 3


def test_naive_evaluation_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="must include timezone"):
        calculate_replenishment(
            inputs(projected_qty="0"),
            evaluated_at=datetime(2026, 9, 3, 20, 0),
        )


def test_invalid_site_timezone_is_rejected() -> None:
    calculation_inputs = inputs(projected_qty="0")

    with pytest.raises(ValidationError, match="valid IANA timezone"):
        ReplenishmentInputs(
            **{
                **calculation_inputs.model_dump(),
                "site_time_zone": "Not/A-Time-Zone",
            }
        )


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
        "whole_number_required": True,
        "lead_time_days": 3,
        "site_time_zone": "Asia/Colombo",
    }
    values[field] = ""

    with pytest.raises(ValidationError):
        ReplenishmentInputs(**values)
