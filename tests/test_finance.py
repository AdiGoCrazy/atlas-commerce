"""Local simulation and provenance-boundary acceptance tests."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from adaptive_commerce.integrations.finance import CfoScenarioImport, FinanceInputs, assess_finance, read_cfo_export
from adaptive_commerce.integrations import observability


def test_base_growth_downside_economics_and_degraded_provenance():
    report = assess_finance(FinanceInputs())
    base, growth, downside = report.scenarios
    assert base.revenue == Decimal("106250")
    assert base.profit == Decimal("12500")
    assert growth.profit > base.profit > downside.profit
    assert report.status == "degraded"
    assert report.source == "local_simulation"
    assert report.simulated
    assert report.recommendation == "approve"


def test_zero_sales_rejects_and_calculates_burn():
    report = assess_finance(FinanceInputs(monthly_traffic=0, campaign_cost=0))
    base = report.scenarios[0]
    assert base.gross_margin is None
    assert base.campaign_roi is None
    assert base.profit == -7500
    assert base.runway_months == Decimal("100000") / Decimal("7500")
    assert report.recommendation == "reject"


@pytest.mark.parametrize("values", [{"conversion_rate": "1.1"}, {"campaign_cost": "-1"}, {"average_order_value": "NaN"}])
def test_invalid_assumptions_rejected(values):
    with pytest.raises(ValidationError):
        FinanceInputs(**values)


def test_cfo_import_requires_provenance_and_validated_outputs():
    with pytest.raises(ValidationError):
        CfoScenarioImport.model_validate({"scenario_id": "invented"})


def test_cfo_export_round_trip_preserves_provenance(tmp_path):
    assumptions = FinanceInputs()
    payload = CfoScenarioImport(
        workspace_id="synthetic-test-workspace", model_id="synthetic-test-model",
        scenario_id="synthetic-test-base", region_id="harbor",
        source_url="https://example.com/synthetic-fixture",
        assumptions=assumptions, result=assess_finance(assumptions).scenarios[0],
    )
    path = tmp_path / "export.json"
    path.write_text(payload.model_dump_json())
    assert read_cfo_export(path) == payload


def test_cfo_export_accepts_export_field_names_and_rejects_malformed_finance():
    payload = {
        "workspace_id": None, "region_id": "harbor", "model_id": "model-123",
        "scenario_id": "default_layer", "source_url": "runway:scenario",
        "assumptions": {
            "monthly_traffic": 50000, "conversion_rate": 0.025,
            "avg_order_value": 85, "product_cost_per_order": 42.77,
            "fulfillment_cost_per_order": 12, "campaign_cost_monthly": 15000,
            "inference_cost_monthly": 3000, "infrastructure_cost_monthly": 500,
            "cash_balance": 30000, "opportunity_cost_monthly": None,
        },
        "result": {
            "scenario_name": "Base", "orders": 1250, "revenue": 106250,
            "product_cost": 53468, "fulfillment_cost": 15000,
            "gross_profit": 37781, "gross_margin": 0.35, "operating_cost": 18500,
            "profit": 19281, "campaign_roi": 1.5, "runway_months": None,
            "opportunity_adjusted_profit": None,
        },
    }
    imported = CfoScenarioImport.validate_payload(payload)
    assert imported.workspace_id is None
    assert imported.assumptions["avg_order_value"] == 85
    assert imported.model_dump(mode="json")["scenario_id"] == "default_layer"

    malformed = {**payload, "result": {**payload["result"], "revenue": "NaN"}}
    with pytest.raises(ValidationError, match="finite"):
        CfoScenarioImport.validate_payload(malformed)

    missing_output = {**payload, "result": {"scenario_name": "Base", "revenue": 1}}
    with pytest.raises(ValidationError, match="missing required outputs"):
        CfoScenarioImport.validate_payload(missing_output)


def test_neatlogs_initializes_once_without_export_when_key_missing(monkeypatch):
    monkeypatch.delenv("NEATLOGS_API_KEY", raising=False)
    calls = []
    monkeypatch.setattr(observability.neatlogs, "init", lambda **kwargs: calls.append(kwargs))
    observability.initialize_tracing.cache_clear()
    try:
        first = observability.initialize_tracing()
        assert observability.initialize_tracing() == first
        assert first.status == "degraded"
        assert len(calls) == 1
        assert calls[0]["disable_export"] is True
    finally:
        observability.initialize_tracing.cache_clear()


def test_neatlogs_initialization_failure_is_explicit(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("synthetic SDK failure")

    monkeypatch.setattr(observability.neatlogs, "init", fail)
    observability.initialize_tracing.cache_clear()
    try:
        assert observability.initialize_tracing().status == "failed"
    finally:
        observability.initialize_tracing.cache_clear()
