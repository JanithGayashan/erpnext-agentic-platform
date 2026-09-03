from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ReplenishmentInputs(BaseModel):
    """Validated facts required by the version-one calculation."""

    item_code: str = Field(min_length=1)
    warehouse: str = Field(min_length=1)
    stock_uom: str = Field(min_length=1)
    projected_qty: Decimal
    reorder_level: Decimal = Field(ge=0)
    reorder_qty: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def require_reorder_value(self) -> "ReplenishmentInputs":
        if self.reorder_level == 0 and self.reorder_qty == 0:
            raise ValueError("reorder level and reorder quantity cannot both be zero")
        return self


class ReplenishmentProposal(BaseModel):
    """Deterministic result; this model does not authorize an ERPNext write."""

    outcome: Literal["PROPOSAL", "NO_ACTION"]
    item_code: str
    warehouse: str
    stock_uom: str
    projected_qty: Decimal
    reorder_level: Decimal
    configured_reorder_qty: Decimal
    deficiency: Decimal
    proposed_stock_qty: Decimal


def calculate_replenishment(
    inputs: ReplenishmentInputs,
) -> ReplenishmentProposal:
    """Calculate a proposal using the documented first-milestone rule."""

    triggered = inputs.projected_qty <= inputs.reorder_level
    deficiency = max(inputs.reorder_level - inputs.projected_qty, Decimal("0"))
    proposed_stock_qty = (
        max(inputs.reorder_qty, deficiency) if triggered else Decimal("0")
    )

    return ReplenishmentProposal(
        outcome="PROPOSAL" if triggered else "NO_ACTION",
        item_code=inputs.item_code,
        warehouse=inputs.warehouse,
        stock_uom=inputs.stock_uom,
        projected_qty=inputs.projected_qty,
        reorder_level=inputs.reorder_level,
        configured_reorder_qty=inputs.reorder_qty,
        deficiency=deficiency,
        proposed_stock_qty=proposed_stock_qty,
    )

