"""Service persistence and Streamlit shell acceptance tests."""

import json
from pathlib import Path
import sqlite3

import neatlogs
import pytest
from streamlit.testing.v1 import AppTest

from adaptive_commerce.application.dashboard import (
    CandidateRequest, bootstrap_database, candidate_report, count_pending_signals,
    list_regions, persist_cfo_import,
    preview_products, submit_candidate,
)
from adaptive_commerce.domain import RegionState
from adaptive_commerce.integrations.finance import CfoScenarioImport, FinanceInputs, assess_finance
from adaptive_commerce.integrations.observability import initialize_tracing
from adaptive_commerce.persistence import connect


def _goto(app: AppTest, section: str) -> None:
    """Navigate through the same sidebar control used by dashboard users."""
    key_by_section = {
        "Ask Atlas": "nav_assistant",
        "Overview": "nav_overview",
        "Region operations": "nav_region_operations",
        "Recommendations": "nav_recommendations",
        "Activity & evidence": "nav_activity",
        "Settings": "nav_settings",
    }
    next(button for button in app.button if button.key == key_by_section[section]).click().run(timeout=15)


@pytest.fixture(autouse=True)
def tracing_lifecycle():
    """Finish the SDK before pytest closes its captured logging streams."""
    initialize_tracing.cache_clear()
    yield
    neatlogs.flush(timeout_millis=1000)
    neatlogs.shutdown(timeout_millis=1000)
    initialize_tracing.cache_clear()


def test_saved_candidate_survives_reload_and_never_activates(tmp_path):
    path = tmp_path / "demo.db"
    bootstrap_database(path)
    result = submit_candidate(path, CandidateRequest(name="New Valley", assumptions=FinanceInputs()))
    bootstrap_database(path)
    assert len(list_regions(path)) == 4
    assert result.region.state == RegionState.candidate
    assert candidate_report(path, result.region.id) == result.finance
    assert preview_products(path, result.region.id) == []
    assert len(preview_products(path, "cedar")) == 5


def test_cfo_provenance_survives_database_reload(tmp_path):
    path = tmp_path / "provenance.db"
    bootstrap_database(path)
    candidate = submit_candidate(path, CandidateRequest(name="Harbor Bay", assumptions=FinanceInputs()))
    imported = CfoScenarioImport(
        workspace_id=None, region_id=candidate.region.id, model_id="model-123",
        scenario_id="default_layer", source_url="runway:scenario", model_ref="Harbor model",
        assumptions={
            "monthly_traffic": 50000, "conversion_rate": 0.025,
            "avg_order_value": 85, "product_cost_per_order": 42.77,
            "fulfillment_cost_per_order": 12, "campaign_cost_monthly": 15000,
            "inference_cost_monthly": 3000, "infrastructure_cost_monthly": 500,
            "cash_balance": 30000,
        },
        result={"scenario_name": "Base", "revenue": 106250, "profit": 1,
                "gross_profit": 2, "product_cost": 3, "fulfillment_cost": 4,
                "gross_margin": 0.3, "operating_cost": 5, "campaign_roi": 0.2,
                "runway_months": None, "opportunity_adjusted_profit": None},
    )

    assert persist_cfo_import(path, imported)
    assert candidate_report(path, candidate.region.id) == candidate.finance
    with connect(path) as conn:
        row = conn.execute(
            "SELECT assumptions, cfo_reference FROM finance_assessments WHERE region_id = ?",
            (candidate.region.id,),
        ).fetchone()
    stored = json.loads(row["assumptions"])
    assert row["cfo_reference"] == "model-123/default_layer"
    assert stored["cfo_ai_import"]["workspace_id"] is None
    assert stored["cfo_ai_import"]["assumptions"]["cash_balance"] == 30000
    assert stored["cfo_ai_import"]["model_ref"] == "Harbor model"


def test_failed_assessment_write_rolls_back_candidate(tmp_path):
    path = tmp_path / "broken.db"
    bootstrap_database(path)
    conn = connect(path)
    try:
        conn.execute("""CREATE TRIGGER fail_assessment BEFORE INSERT ON finance_assessments
                        BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        submit_candidate(path, CandidateRequest(name="Should roll back", assumptions=FinanceInputs()))
    assert len(list_regions(path)) == 3


def test_streamlit_shell_and_candidate_form(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setenv("NEATLOGS_API_KEY", "")
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.run(timeout=15)
    _goto(app, "Region operations")
    assert not app.exception
    assert len(app.selectbox[0].options) == 3
    app.text_input[0].set_value("Demo Coast")
    next(button for button in app.button if button.label == "Assess candidate").click().run(timeout=15)
    assert not app.exception
    assert len(app.selectbox[0].options) == 4
    assert any("Candidate saved" in item.value for item in app.success)
    assert len(app.dataframe) >= 1
    path = tmp_path / "ui.db"
    assert next(r for r in list_regions(path) if r.name == "Demo Coast").state == RegionState.candidate


def test_empty_candidate_name_does_not_write(tmp_path, monkeypatch):
    path = tmp_path / "invalid.db"
    monkeypatch.setenv("ATLAS_DB_PATH", str(path))
    monkeypatch.setenv("NEATLOGS_API_KEY", "")
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.run(timeout=15)
    _goto(app, "Region operations")
    next(button for button in app.button if button.label == "Assess candidate").click().run(timeout=15)
    assert not app.exception
    assert app.error
    assert len(list_regions(path)) == 3


def test_operator_can_assess_and_approve_candidate_in_dashboard(tmp_path, monkeypatch):
    path = tmp_path / "approval-ui.db"
    monkeypatch.setenv("ATLAS_DB_PATH", str(path))
    monkeypatch.setenv("NEATLOGS_API_KEY", "")
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.run(timeout=15)
    _goto(app, "Region operations")
    app.text_input[0].set_value("Approved Coast")
    next(button for button in app.button if button.label == "Assess candidate").click().run(timeout=15)
    assert not app.exception
    approve = next(button for button in app.button if button.key == "approve_region")
    approve.click().run(timeout=15)
    assert not app.exception
    assert any("approved and activated" in item.value for item in app.success)
    approved = next(region for region in list_regions(path) if region.name == "Approved Coast")
    assert approved.state is RegionState.active


def test_dashboard_cycle_persists_and_keeps_operator_section_selected(tmp_path, monkeypatch):
    path = tmp_path / "cycle-ui.db"
    monkeypatch.setenv("ATLAS_DB_PATH", str(path))
    monkeypatch.setenv("NEATLOGS_API_KEY", "")
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.run(timeout=15)
    _goto(app, "Region operations")
    assert app.session_state["dashboard_section"] == "Region operations"
    assert app.selectbox[0].value == "cedar"
    assert count_pending_signals(path, "cedar") == 1

    next(button for button in app.button if button.key == "run_cycle").click().run(timeout=15)

    assert not app.exception
    assert app.session_state["dashboard_section"] == "Region operations"
    assert any("Recommendations updated" in item.value for item in app.success)
    assert count_pending_signals(path, "cedar") == 0
    assert next(button for button in app.button if button.key == "run_cycle").disabled
    with connect(path) as conn:
        row = conn.execute(
            "SELECT status FROM update_cycles WHERE region_id = 'cedar' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert row["status"] == "activated"


def test_chat_is_landing_page_and_sidebar_collapses(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DB_PATH", str(tmp_path / "chat-home.db"))
    monkeypatch.setenv("NEATLOGS_API_KEY", "")
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.run(timeout=15)

    assert not app.exception
    assert app.session_state["dashboard_section"] == "Ask Atlas"
    assert any("Atlas Mind" in element.value for element in app.markdown)
    assert len([button for button in app.button if button.key.startswith("assistant_prompt_")]) == 3

    next(button for button in app.button if button.key == "atlas_sidebar_toggle").click().run(timeout=15)

    assert not app.exception
    assert app.session_state["sidebar_collapsed"] is True
    assert app.session_state["dashboard_section"] == "Ask Atlas"
    for section in ("Overview", "Region operations", "Recommendations",
                    "Activity & evidence", "Settings", "Ask Atlas"):
        _goto(app, section)
        assert not app.exception
        assert app.session_state["dashboard_section"] == section
        assert app.session_state["sidebar_collapsed"] is True

    next(button for button in app.button if button.key == "assistant_prompt_preview").click().run(timeout=15)
    assert not app.exception
    assert "festival" in app.text_area[0].value.lower()
