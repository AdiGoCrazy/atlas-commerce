"""Deterministic simulated finance and an explicit cfo.ai import boundary."""

from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Annotated, Any, Literal

import neatlogs
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    """Validated cfo.ai scenario export; validation is not live verification."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    workspace_id: str | None = None
    region_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    assumptions: dict[str, Any] | FinanceInputs
    result: dict[str, Any] | ScenarioResult

    @field_validator("assumptions", mode="before")
    @classmethod
    def parse_local_assumptions(cls, value: Any) -> Any:
        """Keep round-trips of the app's pre-existing local import shape."""
        if isinstance(value, dict) and "average_order_value" in value and "campaign_cost" in value:
            return FinanceInputs.model_validate(value)
        return value

    @field_validator("result", mode="before")
    @classmethod
    def parse_local_result(cls, value: Any) -> Any:
        """Keep round-trips of the app's existing scenario result shape."""
        if isinstance(value, dict) and value.get("name") in {"base", "growth", "downside"}:
            return ScenarioResult.model_validate(value)
        return value

    @model_validator(mode="after")
    def validate_financial_payload(self) -> "CfoScenarioImport":
        """Reject incomplete or non-finite cfo.ai assumptions and outputs."""
        if isinstance(self.assumptions, FinanceInputs) and isinstance(self.result, ScenarioResult):
            return self

        assumptions = self.assumptions
        result = self.result
        if not isinstance(assumptions, dict) or not isinstance(result, dict):
            raise ValueError("assumptions and result must use one supported finance schema")

        assumption_aliases = {
            "monthly_traffic": ("monthly_traffic",),
            "conversion_rate": ("conversion_rate",),
            "average_order_value": ("avg_order_value", "average_order_value"),
            "product_cost_per_order": ("product_cost_per_order",),
            "fulfillment_cost_per_order": ("fulfillment_cost_per_order",),
            "campaign_cost": ("campaign_cost_monthly", "campaign_cost"),
            "inference_cost": ("inference_cost_monthly", "inference_cost"),
            "infrastructure_cost": ("infrastructure_cost_monthly", "infrastructure_cost"),
            "cash_balance": ("cash_balance",),
        }
        values: dict[str, Any] = {}
        for field_name, aliases in assumption_aliases.items():
            value = next((assumptions[key] for key in aliases if key in assumptions), None)
            if value is None:
                raise ValueError(f"cfo.ai assumptions missing {field_name}")
            values[field_name] = value

        for field_name, value in values.items():
            number = _finite_decimal(value, f"assumptions.{field_name}")
            if number < 0:
                raise ValueError(f"assumptions.{field_name} must be non-negative")
        if _finite_decimal(values["conversion_rate"], "assumptions.conversion_rate") > 1:
            raise ValueError("assumptions.conversion_rate must be at most 1")
        traffic = _finite_decimal(values["monthly_traffic"], "assumptions.monthly_traffic")
        if traffic != traffic.to_integral_value():
            raise ValueError("assumptions.monthly_traffic must be an integer")

        required_outputs = {
            "revenue", "product_cost", "fulfillment_cost", "gross_profit",
            "gross_margin", "operating_cost", "profit", "campaign_roi",
            "runway_months", "opportunity_adjusted_profit",
        }
        if "scenario_name" not in result and "name" not in result:
            raise ValueError("cfo.ai result missing scenario_name")
        missing = required_outputs.difference(result)
        if missing:
            raise ValueError(f"cfo.ai result missing required outputs: {', '.join(sorted(missing))}")
        for field_name in required_outputs:
            value = result[field_name]
            if value is None and field_name in {"gross_margin", "campaign_roi", "runway_months", "opportunity_adjusted_profit"}:
                continue
            _finite_decimal(value, f"result.{field_name}")
        return self

    @classmethod
    def validate_payload(cls, payload: object) -> "CfoScenarioImport":
        """Validate an imported object using this model's strict boundary."""
        return cls.model_validate(payload)


def read_cfo_export(path: Path) -> CfoScenarioImport:
    """Validate operator-supplied JSON; never infer that MCP is authenticated."""
    return CfoScenarioImport.model_validate_json(path.read_text(encoding="utf-8"))


def _finite_decimal(value: Any, field_name: str) -> Decimal:
    """Parse one finite numeric value for the cfo.ai import boundary."""
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field_name} must be numeric") from None
    if not number.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return number


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
