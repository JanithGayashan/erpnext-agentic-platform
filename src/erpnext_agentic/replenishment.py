from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator


class ReplenishmentInputs(BaseModel):
    """Validated facts required by the version-one calculation."""

    item_code: str = Field(min_length=1)
    warehouse: str = Field(min_length=1)
    stock_uom: str = Field(min_length=1)
    projected_qty: Decimal
    reorder_level: Decimal = Field(ge=0)
    reorder_qty: Decimal = Field(ge=0)
    whole_number_required: bool
    lead_time_days: int = Field(ge=0)
    site_time_zone: str = Field(min_length=1)

    @field_validator("site_time_zone")
    @classmethod
    def require_valid_time_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("site time zone is not a valid IANA timezone") from exc
        return value

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
    site_time_zone: str
    transaction_date: date
    schedule_date: date
    lead_time_days: int


def calculate_replenishment(
    inputs: ReplenishmentInputs,
    *,
    evaluated_at: datetime | None = None,
) -> ReplenishmentProposal:
    """Calculate a proposal using the documented first-milestone rule."""

    triggered = inputs.projected_qty <= inputs.reorder_level
    deficiency = max(inputs.reorder_level - inputs.projected_qty, Decimal("0"))
    proposed_stock_qty = (
        max(inputs.reorder_qty, deficiency) if triggered else Decimal("0")
    )
    if triggered and inputs.whole_number_required:
        proposed_stock_qty = proposed_stock_qty.to_integral_value(
            rounding=ROUND_CEILING
        )

    site_zone = ZoneInfo(inputs.site_time_zone)
    if evaluated_at is None:
        local_now = datetime.now(site_zone)
    else:
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must include timezone information")
        local_now = evaluated_at.astimezone(site_zone)
    transaction_date = local_now.date()
    schedule_date = transaction_date + timedelta(days=inputs.lead_time_days)

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
        site_time_zone=inputs.site_time_zone,
        transaction_date=transaction_date,
        schedule_date=schedule_date,
        lead_time_days=inputs.lead_time_days,
    )
