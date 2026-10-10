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
from adaptive_commerce.presentation.view_models import prepare_approval_state

from adaptive_commerce.application.comparison import (
    compare_recommendations,
    list_recommendation_versions,
)

from adaptive_commerce.presentation.view_models import (
    prepare_ranking_comparison,
)

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

    versions = list_recommendation_versions(path, selected)

    if len(versions) < 2:
        st.info(
            "This region needs at least two saved recommendation "
            "versions before a comparison can be displayed."
        )

    else:
        version_labels = {
            version.id: (
                f"{version.id[:8]} — {version.status.value}"
            )
            for version in versions
        }

        before_id = st.selectbox(
            "Before version",
            options=[version.id for version in versions],
            format_func=version_labels.get,
            key="comparison_before",
        )

        after_id = st.selectbox(
            "After version",
            options=[version.id for version in versions],
            index=1,
            format_func=version_labels.get,
            key="comparison_after",
        )

        if before_id == after_id:
            st.warning("Select two different versions to compare.")

        elif st.button("Compare Recommendations"):
            try:
                comparison = compare_recommendations(
                    path,
                    selected,
                    before_id,
                    after_id,
                )

                rows = prepare_ranking_comparison(
                    comparison.before,
                    comparison.after,
                )

                st.write(
                    f"**Before:** {comparison.before_version_id}"
                )
                st.write(
                    f"**After:** {comparison.after_version_id}"
                )

                if rows:
                    st.dataframe(
                        rows,
                        hide_index=True,
                        use_container_width=True,
                    )
                else:
                    st.info(
                        "No eligible products were found "
                        "for either recommendation version."
                    )

            except ValueError as error:
                st.error(f"Comparison unavailable: {error}")
            except sqlite3.Error:
                st.error(
                    "Could not load the comparison data "
                    "from the database."
                )

    
    st.divider()
    st.subheader("Candidate Approval")

    selected_region = next(
        (region for region in regions if region.id == selected),
        None,
    )

    approval_ui = (
    prepare_approval_state(selected_region.state.value)
    if selected_region is not None
    else None
    )
    
    
    if approval_ui is None:
        st.info("Please select a region.")

    elif approval_ui["show_actions"]:
        st.write(f"**Region:** {selected_region.name}")
        st.write(f"**Current status:** {approval_ui['status']}")

        st.warning(
            "Financial recommendations are advisory only. "
            "A region requires explicit human approval."
        )

        col1, col2 = st.columns(2)

        with col1:
            st.button(
                "Approve Candidate",
                disabled=not approval_ui["actions_enabled"],
            )

        with col2:
            st.button(
                "Reject Candidate",
                disabled=not approval_ui["actions_enabled"],
            )

        st.caption(approval_ui["message"])

    elif approval_ui["status"] == "Active":
        st.success(f"{selected_region.name} is already active.")

    elif approval_ui["status"] == "Rejected":
        st.error(f"{selected_region.name} has been rejected.")

    else:
        st.info(approval_ui["message"])
