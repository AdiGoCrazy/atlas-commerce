"""Deterministic simulated finance and an explicit cfo.ai import boundary."""

from decimal import Decimal, localcontext
from pathlib import Path
from typing import Annotated, Literal

import neatlogs
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

Money = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Rate = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
Finite = Annotated[Decimal, Field(allow_inf_nan=False)]


class FinanceInputs(BaseModel):
    """Monthly synthetic assumptions, shared by local and provider models."""

    model_config = ConfigDict(extra="forbid")
    monthly_traffic: int = Field(default=50000, ge=0)
    conversion_rate: Rate = Decimal("0.025")
    average_order_value: Money = Decimal("85")
    product_cost_per_order: Money = Decimal("45")
    fulfillment_cost_per_order: Money = Decimal("12")
    campaign_cost: Money = Decimal("15000")
    inference_cost: Money = Decimal("3000")
    infrastructure_cost: Money = Decimal("4500")
    cash_balance: Money = Decimal("100000")
    opportunity_cost: Money = Decimal("2000")


class ScenarioResult(BaseModel):
    """Simulated monthly economics; undefined ratios are represented as null."""

    model_config = ConfigDict(extra="forbid")
    name: Literal["base", "growth", "downside"]
    orders: Money
    revenue: Money
    product_cost: Money
    fulfillment_cost: Money
    gross_profit: Finite
    gross_margin: Finite | None
    operating_cost: Money
    profit: Finite
    campaign_roi: Finite | None
    runway_months: Money | None
    opportunity_adjusted_profit: Finite


class FinanceReport(BaseModel):
    """Honest provenance and a local decision, never an activation command."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["degraded"] = "degraded"
    source: Literal["local_simulation"] = "local_simulation"
    reason: str = "cfo.ai unavailable: no authenticated model/scenario read verified."
    assumptions: FinanceInputs
    scenarios: list[ScenarioResult]
    recommendation: Literal["approve", "reject"]
    simulated: Literal[True] = True


class CfoScenarioImport(BaseModel):
    """Import contract for actual exported outputs; import is not live verification."""

    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=1)
    region_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    source_url: HttpUrl
    assumptions: FinanceInputs
    result: ScenarioResult


def read_cfo_export(path: Path) -> CfoScenarioImport:
    """Validate operator-supplied JSON; never infer that MCP is authenticated."""
    return CfoScenarioImport.model_validate_json(path.read_text(encoding="utf-8"))


@neatlogs.span(kind="CHAIN", name="simulate_financial_scenarios")
def assess_finance(inputs: FinanceInputs) -> FinanceReport:
    """Compare three deterministic scenarios with no network or activation."""
    scenarios = []
    with localcontext() as context:
        context.prec = 28
        for name, multiplier in (
            ("base", Decimal("1")),
            ("growth", Decimal("1.25")),
            ("downside", Decimal("0.75")),
        ):
            orders = inputs.monthly_traffic * inputs.conversion_rate * multiplier
            revenue = orders * inputs.average_order_value
            product_cost = orders * inputs.product_cost_per_order
            fulfillment = orders * inputs.fulfillment_cost_per_order
            gross = revenue - product_cost - fulfillment
            operating = inputs.campaign_cost + inputs.inference_cost + inputs.infrastructure_cost
            profit = gross - operating
            scenarios.append(ScenarioResult(
                name=name, orders=orders, revenue=revenue, product_cost=product_cost,
                fulfillment_cost=fulfillment, gross_profit=gross,
                gross_margin=gross / revenue if revenue else None,
                operating_cost=operating, profit=profit,
                campaign_roi=(gross - inputs.campaign_cost) / inputs.campaign_cost
                if inputs.campaign_cost else None,
                runway_months=inputs.cash_balance / -profit if profit < 0 else None,
                opportunity_adjusted_profit=profit - inputs.opportunity_cost,
            ))
    base = scenarios[0]
    return FinanceReport(
        assumptions=inputs, scenarios=scenarios,
        recommendation="approve" if (
            base.opportunity_adjusted_profit > 0
            and base.gross_margin is not None and base.gross_margin >= Decimal("0.2")
        ) else "reject",
    )
