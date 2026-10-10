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
from adaptive_commerce.application.regions import (
    approve_candidate, compare_recommendations, recommendation_versions, reject_candidate,
)
from adaptive_commerce.integrations.finance import FinanceInputs, read_cfo_export
from adaptive_commerce.integrations.observability import initialize_tracing
from adaptive_commerce.presentation.view_models import prepare_ranking_comparison


def render_dashboard() -> None:
    """Render seeded regions, candidate form, finance results, and product preview."""
    st.set_page_config(page_title="Atlas Commerce", layout="wide")
    st.title("Atlas Commerce")
    st.caption("Synthetic demo data • Financial results and recommendations are simulated")
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
            st.success("Candidate saved and assessed. It remains inactive until approved.")
        except ValidationError:
            st.error("Enter a non-empty region name and valid financial assumptions.")
        except sqlite3.Error:
            st.error("The candidate could not be saved. No partial candidate assessment was committed.")

    regions = list_regions(path)
    regions_by_id = {region.id: region for region in regions}
    labels = {region.id: f"{region.name} — {region.state.value}" for region in regions}
    selected = st.selectbox("Region", options=list(labels), format_func=labels.get, key="selected_region")
    selected_region = regions_by_id[selected]
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

        st.write(f"**Region:** {labels[selected]}")
        st.write(f"**Assessment status:** {report.status}")
        st.write(f"**Recommendation:** {report.recommendation.upper()}")

        st.caption(
            "These are simulated financial results. "
            "A recommendation does not activate a region."
        )

        st.subheader("Financial scenarios")
        st.dataframe(
            [
                scenario.model_dump(mode="json")
                for scenario in report.scenarios
            ],
            hide_index=True,
        )
        for scenario in report.scenarios:
            st.markdown(f"### {scenario.name.title()} scenario")

            col1, col2, col3 = st.columns(3)

            col1.metric(
                "Revenue",
                f"{scenario.revenue:,.2f}"
            )

            col2.metric(
                "Profit",
                f"{scenario.profit:,.2f}"
            )

            col3.metric(
                "Gross profit",
                f"{scenario.gross_profit:,.2f}"
            )

            col4, col5 = st.columns(2)

            col4.metric(
                "Operating cost",
                f"{scenario.operating_cost:,.2f}"
            )

            if scenario.gross_margin is not None:
                col5.metric(
                    "Gross margin",
                    f"{scenario.gross_margin:.2%}"
                )
            else:
                col5.metric("Gross margin", "N/A")

            st.divider()

        with st.expander("Financial assumptions"):
            st.json(report.assumptions.model_dump(mode="json"))

    else:
        st.info(
            "No saved financial assessment available "
            "for this region."
        )

    if selected_region.state.value == "candidate":
        st.subheader("Candidate approval")
        approve_col, reject_col = st.columns(2)
        if approve_col.button("Approve candidate", key=f"approve-{selected}"):
            if approve_candidate(path, selected):
                st.success("Candidate approved and activated with an initial recommendation version.")
                st.rerun()
            st.error("Candidate could not be approved; it may no longer be awaiting a decision.")
        if reject_col.button("Reject candidate", key=f"reject-{selected}"):
            if reject_candidate(path, selected):
                st.success("Candidate rejected and remains inactive.")
                st.rerun()
            st.error("Candidate could not be rejected; it may no longer be awaiting a decision.")

    
    st.subheader("Top 5 Product Recommendations")

    ranked = preview_products(path, selected)

    if not ranked:
        st.info(
            "No recommendation profile available for this region yet."
        )
    else:
        top_five = ranked[:5]

        st.caption(
            f"Showing {len(top_five)} recommended products "
            "for the selected region."
        )

        for item in top_five:
            with st.container(border=True):
                left, right = st.columns([3, 1])

                with left:
                    st.markdown(
                        f"### #{item.rank} — {item.product_id}"
                    )

                with right:
                    st.metric(
                        "Ranking score",
                        f"{item.score:.3f}"
                    )

                with st.expander("Why this product?"):
                    st.json(
                        item.breakdown.model_dump(mode="json")
                    )
    st.divider()
    st.subheader("Before / After Recommendations")
    versions = recommendation_versions(path, selected)
    if len(versions) < 2:
        st.info("At least two saved recommendation versions for this region are needed to compare rankings.")
    else:
        version_labels = {version.id: f"{version.id} ({version.status.value})" for version in versions}
        before_id, after_id = st.columns(2)
        before = before_id.selectbox(
            "Before version", options=[version.id for version in versions],
            format_func=version_labels.get, key=f"before-{selected}", index=len(versions) - 1,
        )
        after = after_id.selectbox(
            "After version", options=[version.id for version in versions],
            format_func=version_labels.get, key=f"after-{selected}", index=0,
        )
        comparison = compare_recommendations(path, selected, before, after)
        st.caption(f"Both rankings evaluated at {comparison.evaluated_at.isoformat()}.")
        st.dataframe(
            prepare_ranking_comparison(comparison.before, comparison.after),
            hide_index=True,
        )
        before_column, after_column = st.columns(2)
        for column, heading, ranking in (
            (before_column, "Before", comparison.before),
            (after_column, "After", comparison.after),
        ):
            with column:
                st.markdown(f"#### {heading} — top {len(ranking)}")
                for item in ranking:
                    with st.container(border=True):
                        st.write(f"#{item.rank} {item.product_id} — {item.score:.3f}")
                        with st.expander(f"Why {item.product_id}?"):
                            st.json(item.breakdown.model_dump(mode="json"))
