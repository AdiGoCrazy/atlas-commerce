"""Service persistence and Streamlit shell acceptance tests."""

from pathlib import Path
import sqlite3

import neatlogs
import pytest
from streamlit.testing.v1 import AppTest

from adaptive_commerce.application.dashboard import (
    CandidateRequest, bootstrap_database, candidate_report, list_regions,
    preview_products, submit_candidate,
)
from adaptive_commerce.domain import RegionState
from adaptive_commerce.integrations.finance import FinanceInputs
from adaptive_commerce.integrations.observability import initialize_tracing
from adaptive_commerce.persistence import connect


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
    monkeypatch.delenv("NEATLOGS_API_KEY", raising=False)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.run(timeout=15)
    assert not app.exception
    assert len(app.selectbox[0].options) == 3
    app.text_input[0].set_value("Demo Coast")
    app.button[0].click().run(timeout=15)
    assert not app.exception
    assert len(app.selectbox[0].options) == 4
    assert any("Candidate saved" in item.value for item in app.success)
    assert any("local fallback" in item.value for item in app.warning)
    assert len(app.dataframe) == 1
    path = tmp_path / "ui.db"
    assert next(r for r in list_regions(path) if r.name == "Demo Coast").state == RegionState.candidate


def test_empty_candidate_name_does_not_write(tmp_path, monkeypatch):
    path = tmp_path / "invalid.db"
    monkeypatch.setenv("ATLAS_DB_PATH", str(path))
    monkeypatch.delenv("NEATLOGS_API_KEY", raising=False)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.run(timeout=15)
    app.button[0].click().run(timeout=15)
    assert not app.exception
    assert app.error
    assert len(list_regions(path)) == 3
