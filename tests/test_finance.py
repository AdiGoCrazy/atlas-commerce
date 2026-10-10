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
