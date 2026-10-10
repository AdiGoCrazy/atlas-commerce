"""Streamlit shell: presentation delegates all state and finance to services."""

import os
import sqlite3
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from adaptive_commerce.application.dashboard import (
    CandidateRequest, bootstrap_database, candidate_report, list_regions, persist_cfo_import,
    preview_products, submit_candidate,
)
from adaptive_commerce.integrations.finance import FinanceInputs, read_cfo_export
from adaptive_commerce.integrations.observability import initialize_tracing


def render_dashboard() -> None:
    """Render seeded regions, candidate form, finance results, and product preview."""
    st.set_page_config(page_title="Atlas Commerce", layout="wide")
    st.title("Atlas Commerce")
    st.caption("Synthetic demo data • Financial results are simulated • Sprint 1 foundation")
    path = Path(os.environ.get("ATLAS_DB_PATH", "data/atlas_commerce.db"))
    try:
        bootstrap_database(path)
    except (OSError, sqlite3.Error, ValidationError, ValueError):
        st.error("The demo database could not be initialized. Check its path and fixture data.")
        return
    tracing = initialize_tracing()
    with st.expander("Integration status", expanded=True):
        st.write(f"neatlogs — {tracing.status}: {tracing.detail}")
        st.warning("cfo.ai unavailable — local fallback only; no verified model/scenario connection.")
        st.write("Entire — development evidence is recorded in docs/evidence.md; it does not store runtime weights.")

    with st.form("candidate"):
        st.subheader("Candidate region")
        name = st.text_input("Region name")
        traffic = st.number_input("Monthly traffic", min_value=0, value=50000, step=1000)
        conversion = st.number_input("Conversion rate", min_value=0.0, max_value=1.0, value=0.025, format="%.3f")
        order_value = st.number_input("Average order value", min_value=0.0, value=85.0)
        submitted = st.form_submit_button("Save candidate and assess")
    if submitted:
        try:
            request = CandidateRequest(name=name, assumptions=FinanceInputs(
                monthly_traffic=traffic, conversion_rate=str(conversion), average_order_value=str(order_value),
            ))
            result = submit_candidate(path, request)
            st.session_state["selected_region"] = result.region.id
            st.success("Candidate saved. It remains inactive until human approval is implemented in Sprint 2.")
        except ValidationError:
            st.error("Enter a non-empty region name and valid financial assumptions.")
        except sqlite3.Error:
            st.error("The candidate could not be saved. No partial candidate assessment was committed.")

    regions = list_regions(path)
    labels = {region.id: f"{region.name} — {region.state.value}" for region in regions}
    selected = st.selectbox("Region", options=list(labels), format_func=labels.get, key="selected_region")
    report = candidate_report(path, selected)
    st.subheader("Financial assessment")
    export_path = os.environ.get("CFO_SCENARIO_PATH")
    if export_path:
        try:
            imported = read_cfo_export(Path(export_path))
            if imported.region_id == selected:
                if persist_cfo_import(path, imported):
                    st.success("Validated cfo.ai assumptions and references saved with this finance assessment.")
                else:
                    st.warning("cfo.ai import is valid but no finance assessment exists for this region; provenance was not saved.")
                st.warning("Operator-imported cfo.ai output; live connection and provenance are unverified.")
                st.json(imported.model_dump(mode="json"))
        except (OSError, ValidationError):
            st.error("The cfo.ai export could not be read or failed validation. Local fallback remains available.")
    if report:
        st.warning(report.reason)
        st.write(f"Simulated recommendation: {report.recommendation}. This does not activate the region.")
        st.dataframe([item.model_dump(mode="json") for item in report.scenarios], hide_index=True)
        with st.expander("Model assumptions and ratio definitions"):
            st.json(report.assumptions.model_dump(mode="json"))
            st.caption("Monthly gross profit subtracts product and fulfillment costs. Campaign ROI = (gross profit − campaign spend) / campaign spend. Null runway means no operating burn; null ROI means zero spend.")
    else:
        st.info("No saved form assessment. Seeded finance is synthetic and is not verified cfo.ai evidence.")
    st.subheader("Storefront preview")
    ranked = preview_products(path, selected)
    if not ranked:
        st.info("No active recommendation profile for this candidate.")
    for item in ranked:
        st.write(f"{item.rank}. {item.product_id} — score {item.score:.3f}")
        with st.expander(f"Why {item.product_id}?"):
            st.json(item.breakdown.model_dump(mode="json"))
