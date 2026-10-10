"""Professional local operator dashboard for Atlas Commerce."""

import os
import sqlite3
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from adaptive_commerce.application.dashboard import (
    CandidateRequest, bootstrap_database, candidate_report, count_pending_signals,
    get_product_catalog,
    list_region_actions, list_region_cycles, list_regions, persist_cfo_import,
    preview_products, submit_candidate,
)
from adaptive_commerce.application.assistant import ask_assistant, assistant_status
from adaptive_commerce.application.lifecycle import (
    approve_region, archive_region, pause_region, reject_region, resume_region,
    rollback_region,
)
from adaptive_commerce.application.operator import inject_demo_signal, reset_demo
from adaptive_commerce.application.worker import replay_cycle, run_all_active_regions, run_region_cycle
from adaptive_commerce.domain import RegionState
from adaptive_commerce.integrations.finance import FinanceInputs, read_cfo_export
from adaptive_commerce.integrations.observability import initialize_tracing


def render_dashboard() -> None:
    """Render summary, region controls, recommendations, and audit evidence."""
    st.set_page_config(page_title="Atlas Commerce", page_icon="◈", layout="wide")
    st.markdown("""
    <style>
      .block-container {padding-top: 1.8rem; padding-bottom: 2.5rem; max-width: 1440px;}
      [data-testid="stMetric"] {
        background:var(--secondary-background-color);
        color:var(--text-color);
        border:1px solid rgba(128, 128, 128, .24);
        padding:14px 16px;
        border-radius:12px;
      }
      [data-testid="stMetricLabel"], [data-testid="stMetricLabel"] p,
      [data-testid="stMetricValue"], [data-testid="stMetricDelta"] {
        color:var(--text-color) !important;
      }
      [data-testid="stMetricLabel"] p {font-size:.82rem;opacity:.76;}
      h1 {letter-spacing:-.035em;} .atlas-muted {color:#667085;font-size:.9rem;}
      .atlas-badge {display:inline-block;padding:.2rem .55rem;border-radius:999px;background:#eef4ff;color:#214c9a;font-size:.78rem;}
    </style>
    """, unsafe_allow_html=True)
    path = Path(os.environ.get("ATLAS_DB_PATH", "data/atlas_commerce.db"))
    st.title("Atlas Commerce")
    st.caption("Regional commerce intelligence · Operator workspace")
    flash = st.session_state.pop("flash_message", None)
    if flash:
        level = st.session_state.pop("flash_level", "success")
        getattr(st, level)(flash)
    try:
        bootstrap_database(path)
    except (OSError, sqlite3.Error, ValidationError, ValueError):
        st.error("The workspace database could not be initialized. Check its path and fixture data.")
        return
    tracing = initialize_tracing()
    st.caption(f"Observability · neatlogs {tracing.status.title()} — {tracing.detail}")
    _integration_status(tracing.status, tracing.detail)
    regions = list_regions(path)
    if not regions:
        st.info("No regions found. Restore the demo data from Settings.")
        return
    labels = {region.id: f"{region.name} · {region.state.value.replace('_', ' ').title()}"
              for region in regions}
    default_region = st.session_state.get("selected_region")
    if default_region not in labels:
        active_regions = [item for item in regions if item.state is RegionState.active]
        default_region = next(
            (item.id for item in active_regions if count_pending_signals(path, item.id)),
            active_regions[0].id if active_regions else regions[0].id,
        )

    section = st.radio(
        "Dashboard section",
        ["Overview", "Region operations", "Recommendations", "Activity & evidence", "Atlas Assistant", "Settings"],
        horizontal=True,
        label_visibility="collapsed",
        key="dashboard_section",
    )
    if section == "Overview":
        _render_overview(path, regions)
    elif section == "Region operations":
        _render_region_operations(path, regions, labels, default_region)
    selected_region = st.session_state.get("selected_region", default_region)
    if selected_region not in labels:
        selected_region = default_region
    elif section == "Recommendations":
        _render_recommendations(path, selected_region)
    elif section == "Activity & evidence":
        _render_activity(path, selected_region)
    elif section == "Atlas Assistant":
        _render_assistant(path)
    elif section == "Settings":
        _render_settings(path)


def _integration_status(neat_status: str, neat_detail: str) -> None:
    with st.expander("Integration status", expanded=False):
        left, middle, right = st.columns(3)
        left.markdown("**neatlogs**")
        left.caption(f"{neat_status.title()} · {neat_detail}")
        middle.markdown("**cfo.ai**")
        middle.caption("Degraded · Local financial simulation; live scenario read not verified in this session.")
        right.markdown("**Entire**")
        right.caption("Checkpoint evidence is documented; entire-graph is unavailable in this environment.")


def _render_overview(path: Path, regions) -> None:
    active = [item for item in regions if item.state is RegionState.active]
    candidates = [item for item in regions if item.state is RegionState.candidate]
    latest = []
    for region in regions:
        latest.extend(list_region_cycles(path, region.id)[:1])
    latest.sort(key=lambda item: item.created_at, reverse=True)
    last_status = latest[0].status.value if latest else "No cycles yet"
    metric_cols = st.columns(4)
    metric_cols[0].metric("Active regions", len(active))
    metric_cols[1].metric("Awaiting review", len(candidates))
    metric_cols[2].metric("Latest cycle", last_status.replace("_", " ").title())
    metric_cols[3].metric("Products in catalog", len(get_product_catalog(path)))
    st.subheader("Regional portfolio")
    st.caption("Synthetic signal and commerce data · Financial and evaluation results are simulated.")
    rows = [{"Region": item.name, "Status": item.state.value.replace("_", " ").title(),
             "Locale": item.locale} for item in regions]
    st.dataframe(rows, hide_index=True, width="stretch")
    st.subheader("Latest activity")
    if latest:
        for cycle in latest[:4]:
            status = cycle.status.value.title()
            st.markdown(f"**{cycle.region_id}** · {status} · {cycle.created_at.astimezone().strftime('%b %d, %H:%M')} ")
            if cycle.rejection_reason:
                st.caption(cycle.rejection_reason.replace("_", " "))
    else:
        st.info("No autonomous cycles have run. Review a candidate or run a cycle from Region operations.")


def _render_region_operations(path: Path, regions, labels: dict[str, str], default_region: str) -> None:
    st.subheader("Add a candidate region")
    with st.form("candidate"):
        col1, col2 = st.columns([1.2, 1])
        name = col1.text_input("Region name", placeholder="e.g. Harbor Bay")
        monthly_traffic = col2.number_input("Monthly traffic", min_value=0, value=50000, step=1000)
        with st.expander("Financial assumptions", expanded=False):
            a, b, c = st.columns(3)
            conversion = a.number_input("Conversion rate", min_value=0.0, max_value=1.0,
                                        value=0.025, format="%.3f")
            order_value = b.number_input("Average order value", min_value=0.0, value=85.0)
            product_cost = c.number_input("Product cost per order", min_value=0.0, value=45.0)
            d, e, f = st.columns(3)
            fulfillment = d.number_input("Fulfillment per order", min_value=0.0, value=12.0)
            campaign = e.number_input("Monthly campaign spend", min_value=0.0, value=15000.0)
            infrastructure = f.number_input("Monthly infrastructure", min_value=0.0, value=4500.0)
        submitted = st.form_submit_button("Assess candidate", type="primary")
    if submitted:
        try:
            result = submit_candidate(path, CandidateRequest(
                name=name,
                assumptions=FinanceInputs(
                    monthly_traffic=monthly_traffic,
                    conversion_rate=str(conversion),
                    average_order_value=str(order_value),
                    product_cost_per_order=str(product_cost),
                    fulfillment_cost_per_order=str(fulfillment),
                    campaign_cost=str(campaign),
                    infrastructure_cost=str(infrastructure),
                ),
            ))
            st.session_state["selected_region"] = result.region.id
            st.session_state["flash_message"] = "Candidate saved and assessed. It remains inactive until an operator approves it."
            st.session_state["flash_level"] = "success"
            st.rerun()
        except ValidationError:
            st.error("Enter a region name and valid non-negative financial assumptions.")
        except sqlite3.Error:
            st.error("The candidate could not be saved. No partial assessment was committed.")

    st.divider()
    st.subheader("Selected region")
    if st.session_state.get("selected_region") not in labels:
        st.session_state["selected_region"] = default_region
    selected = st.selectbox("Region", options=list(labels), format_func=labels.get,
                            key="selected_region")
    region = next(item for item in regions if item.id == selected)
    report = candidate_report(path, selected)
    export_path = os.environ.get("CFO_SCENARIO_PATH")
    if export_path:
        try:
            imported = read_cfo_export(Path(export_path))
            if imported.region_id == selected and persist_cfo_import(path, imported):
                st.warning("Operator-supplied cfo.ai export is attached; live connection and source provenance remain unverified.")
        except (OSError, ValidationError):
            st.error("The configured cfo.ai export could not be read or failed validation. Local simulation remains available.")
    st.caption(f"Lifecycle status · **{region.state.value.replace('_', ' ').title()}**")
    if report:
        _finance_summary(report)
    else:
        st.info("No candidate finance assessment is available for this region.")

    pending_count = count_pending_signals(path, selected) if region.state is RegionState.active else 0
    if region.state is RegionState.active:
        if pending_count:
            st.caption(f"{pending_count} new signal{'s' if pending_count != 1 else ''} waiting to be evaluated.")
        else:
            st.info("No new signals are waiting. Add synthetic evidence below to create another update cycle.")

    controls = st.columns(5)
    if region.state is RegionState.candidate:
        if controls[0].button("Approve region", key="approve_region", type="primary"):
            _run_operator_action(lambda: approve_region(path, selected), "Region approved and activated.")
        if controls[1].button("Reject", key="reject_region"):
            _run_operator_action(lambda: reject_region(path, selected), "Candidate rejected.")
    elif region.state is RegionState.active:
        if controls[0].button("Run regional cycle", key="run_cycle", type="primary",
                              disabled=pending_count == 0):
            _run_cycle_action(lambda: run_region_cycle(path, selected))
        if controls[1].button("Pause", key="pause_region"):
            _run_operator_action(lambda: pause_region(path, selected), "Region paused.")
        if controls[2].button("Rollback", key="rollback_region"):
            _run_operator_action(lambda: rollback_region(path, selected), "Previous recommendation version restored.")
        if controls[3].button("Archive", key="archive_region"):
            _run_operator_action(lambda: archive_region(path, selected), "Region archived.")
    elif region.state is RegionState.paused:
        if controls[0].button("Resume", key="resume_region", type="primary"):
            _run_operator_action(lambda: resume_region(path, selected), "Region resumed.")
        if controls[1].button("Archive", key="archive_paused_region"):
            _run_operator_action(lambda: archive_region(path, selected), "Region archived.")

    if region.state is RegionState.active:
        st.markdown("#### Add synthetic evidence")
        with st.form("signal_injection"):
            topics = st.text_input("Topics", value="festival", help="Comma-separated deterministic topic labels")
            confidence = st.slider("Confidence", min_value=0.0, max_value=1.0, value=0.7, step=0.05)
            st.caption(
                f"Confidence scales each topic proposal: {confidence:.2f} × 5 = "
                f"{confidence * 5:.2f} before the region's change limit. Values below 0.70 are filtered."
            )
            evidence = st.text_area("Evidence excerpt", value="Synthetic community signal indicates rising interest in festival accessories.")
            add_signal = st.form_submit_button("Add synthetic signal")
        if add_signal:
            try:
                pending_before = count_pending_signals(path, selected)
                signal = inject_demo_signal(path, selected, [part.strip() for part in topics.split(",") if part.strip()],
                                            confidence, evidence)
                if count_pending_signals(path, selected) > pending_before:
                    st.success(f"Synthetic signal {signal.id} added to the region inbox.")
                else:
                    st.info(
                        f"This evidence already exists as {signal.id}; no duplicate was added. "
                        "Change the excerpt to represent a separate observation."
                    )
            except (ValueError, ValidationError, sqlite3.Error) as error:
                st.error(f"Signal was not added: {error}")
    if controls[4].button("Run all active regions", key="run_all"):
        try:
            results = run_all_active_regions(path)
            for result in results:
                st.write(f"{result.cycle.region_id}: {result.cycle.status.value}")
        except sqlite3.Error:
            st.error("The regional pass could not be persisted.")

    if report and region.state is RegionState.candidate:
        st.caption("The finance result is a recommendation; activation requires the explicit approval action above.")


def _finance_summary(report) -> None:
    st.markdown("#### Financial assessment")
    st.caption(f"Financial model · {report.status.title()} · {report.reason} · Simulated monthly estimates")
    base = report.scenarios[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Base revenue", f"${base.revenue:,.0f}")
    c2.metric("Contribution profit", f"${base.profit:,.0f}")
    c3.metric("Gross margin", f"{base.gross_margin:.1%}" if base.gross_margin is not None else "—")
    c4.metric("Decision", report.recommendation.title())
    with st.expander("Base, growth, and downside assumptions"):
        st.dataframe([scenario.model_dump(mode="json") for scenario in report.scenarios],
                     hide_index=True, width="stretch")
        st.json(report.assumptions.model_dump(mode="json"))


def _render_recommendations(path: Path, region_id: str) -> None:
    regions = {item.id: item for item in list_regions(path)}
    st.subheader(f"Recommendations · {regions[region_id].name}")
    st.caption("Rankings are deterministic; all sales and evaluation metrics are simulated.")
    ranked = preview_products(path, region_id)
    catalog = get_product_catalog(path)
    if not ranked:
        st.info("This region has no active recommendation profile.")
        return
    current = st.columns(5)
    for column, item in zip(current, ranked[:5]):
        product = catalog[item.product_id]
        with column:
            st.markdown(f"**#{item.rank} · {product.name}**")
            st.caption(f"${product.price:.2f} · {'In stock' if product.in_stock else 'Out of stock'}")
            st.metric("Score", f"{item.score:.2f}")
            with st.expander("Why this product"):
                st.json(item.breakdown.model_dump(mode="json"))
    cycles = list_region_cycles(path, region_id)
    last = next((item for item in cycles if item.evaluation and item.evaluation.get("after")), None)
    if last is None:
        st.info("Run a regional cycle to see a persisted before/after comparison.")
        return
    st.divider()
    st.markdown("#### Latest before / after")
    st.caption(f"Cycle {last.id} · {last.status.value.title()} · {last.rejection_reason or 'Activated'}")
    _render_comparison(last.evaluation.get("before", []), last.evaluation.get("after", []), catalog)
    metrics = last.evaluation
    a, b, c, d = st.columns(4)
    a.metric("Active relevance", f"{metrics.get('active_relevance', 0):.2f}")
    b.metric("Candidate relevance", f"{metrics.get('candidate_relevance', 0):.2f}")
    c.metric("Active simulated profit", f"${metrics.get('active_profit', 0):.2f}")
    d.metric("Candidate simulated profit", f"${metrics.get('candidate_profit', 0):.2f}")


def _render_comparison(before: list[dict], after: list[dict], catalog: dict) -> None:
    before_rank = {item["product_id"]: item for item in before}
    after_rank = {item["product_id"]: item for item in after}
    ids = sorted(set(before_rank) | set(after_rank))
    rows = []
    for product_id in ids:
        old, new = before_rank.get(product_id), after_rank.get(product_id)
        product = catalog.get(product_id)
        rows.append({
            "Product": product.name if product else product_id,
            "Before": old["rank"] if old else "—",
            "After": new["rank"] if new else "—",
            "Movement": (f"↑ {old['rank'] - new['rank']}" if old and new and new["rank"] < old["rank"]
                         else f"↓ {new['rank'] - old['rank']}" if old and new and new["rank"] > old["rank"]
                         else "New" if new and not old else "Removed" if old and not new else "—"),
        })
    st.dataframe(rows, hide_index=True, width="stretch")


def _render_activity(path: Path, region_id: str) -> None:
    st.subheader("Cycle history")
    cycles = list_region_cycles(path, region_id)
    if cycles:
        st.dataframe([{
            "Cycle": item.id,
            "Status": item.status.value.title(),
            "Signals": len(item.signal_ids),
            "Reason": item.rejection_reason or "—",
            "Started": item.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        } for item in cycles], hide_index=True, width="stretch")
        details_by_id = {item.id: item for item in cycles}
        detail_id = st.selectbox(
            "Inspect cycle outcome",
            options=list(details_by_id),
            format_func=lambda item_id: f"{details_by_id[item_id].status.value.title()} · {item_id[:8]}",
            key="inspect_cycle",
        )
        selected_cycle = details_by_id[detail_id]
        if selected_cycle.evaluation:
            st.caption("Includes signal gate reasons and simulated before/after evaluation when available.")
            st.json(selected_cycle.evaluation)
        replayable = [item for item in cycles if item.signal_ids]
        if replayable:
            options = {item.id: f"{item.created_at.astimezone().strftime('%b %d %H:%M')} · {item.status.value} · {item.id[:8]}"
                       for item in replayable}
            selected = st.selectbox("Replay source cycle", list(options), format_func=options.get, key="replay_source")
            if st.button("Replay cycle with the same signals", key="replay_cycle"):
                _run_cycle_action(lambda: replay_cycle(path, selected))
    else:
        st.info("No cycles recorded for this region.")
    st.subheader("Operator history")
    actions = list_region_actions(path, region_id)
    if actions:
        st.dataframe([{
            "Action": item["action"].replace("_", " ").title(),
            "When": item["created_at"],
            "Details": str(item["detail"]),
        } for item in actions], hide_index=True, width="stretch")
    else:
        st.caption("Approvals, pauses, resumes, archives, and rollbacks appear here.")


def _render_settings(path: Path) -> None:
    st.subheader("Demo workspace")
    st.caption("Reset restores the checked-in synthetic regions, catalog, signals, and event windows.")
    confirm = st.checkbox("I understand that reset clears local candidates, cycles, and operator history.")
    if st.button("Reset and replay from fixtures", disabled=not confirm, key="reset_demo"):
        try:
            reset_demo(path)
            st.session_state.pop("selected_region", None)
            st.session_state["flash_message"] = "Demo state restored from fixtures. Cedar has one new festival signal ready to run."
            st.session_state["flash_level"] = "success"
            st.rerun()
        except sqlite3.Error:
            st.error("Demo state could not be reset.")


def _render_assistant(path: Path) -> None:
    """Render grounded questions, evidence references, and correction alerts."""
    st.subheader("Atlas Assistant")
    status = assistant_status()
    st.caption(f"{status['provider']} · {status['model']} · {status['status'].title()} — {status['detail']}")
    st.info(
        f"Questions use the configured {status['provider']} model. Relevant Atlas records, including synthetic "
        "signal excerpts, may be sent to that provider. Finance and recommendation outcomes remain "
        "grounded in Atlas records and the deterministic engine. The assistant cannot activate a region "
        "or recommendation version; a requested signal correction is separately audited."
    )
    history: list[dict[str, str]] = st.session_state.setdefault("assistant_history", [])
    for item in history:
        with st.chat_message(item["role"]):
            st.markdown(item["content"])
            for citation in item.get("evidence", []):
                st.caption(f"{citation['kind']} · {citation['label']} · `{citation['record_id']}`")
            if item.get("trace_id"):
                st.caption(f"Neatlogs trace · `{item['trace_id']}`")
            if item.get("repaired_signal_id"):
                st.success(f"Signal correction saved as `{item['repaired_signal_id']}`; the original signal is unchanged.")
            for notice in item.get("notices", []):
                st.warning(notice)
    with st.form("atlas_assistant_form", clear_on_submit=True):
        question = st.text_area("Ask about your regions, recommendations, finance, or a cycle",
                                placeholder="Why did Trail Boots move down in the last cycle?", height=90)
        submitted = st.form_submit_button("Ask Atlas", type="primary")
    if submitted:
        history.append({"role": "user", "content": question})
        result = ask_assistant(path, question,
            history=[{"role": item["role"], "content": item["content"]} for item in history[:-1]])
        history.append({"role": "assistant", "content": result.answer,
                        "evidence": [item.model_dump(mode="json") for item in result.evidence],
                        "trace_id": result.trace_id, "notices": result.notices,
                        "repaired_signal_id": result.repaired_signal_id})
        st.session_state["assistant_history"] = history[-20:]
        st.rerun()


def _run_operator_action(action, success_message: str) -> None:
    try:
        result = action()
        st.session_state["flash_message"] = f"{success_message} {result}" if isinstance(result, str) else success_message
        st.session_state["flash_level"] = "success"
        st.rerun()
    except (ValueError, sqlite3.Error) as error:
        st.error(str(error))


def _run_cycle_action(action) -> None:
    try:
        result = action()
        status = result.cycle.status.value
        if status == "activated":
            st.session_state["flash_message"] = f"Recommendations updated. Cycle {result.cycle.id} activated."
            st.session_state["flash_level"] = "success"
        elif result.cycle.rejection_reason == "no_actionable_signals":
            rejected = (result.cycle.evaluation or {}).get("rejections", [])
            reasons = sorted({item.get("reason", "ineligible evidence") for item in rejected})
            detail = f" Signal checks: {', '.join(reasons)}." if reasons else ""
            st.session_state["flash_message"] = (
                "No recommendation update. No eligible new evidence was found; "
                f"the active version is unchanged.{detail}"
            )
            st.session_state["flash_level"] = "warning"
        elif status == "rejected":
            metrics = result.cycle.evaluation or {}
            minimum_retention = float(metrics.get("min_profit_retention", 0.85))
            st.session_state["flash_message"] = (
                "Proposed update did not pass the evaluation guardrail; the active version is unchanged. "
                f"Simulated relevance {metrics.get('active_relevance', 0):.2f} → "
                f"{metrics.get('candidate_relevance', 0):.2f}; profit "
                f"${metrics.get('active_profit', 0):.2f} → ${metrics.get('candidate_profit', 0):.2f}. "
                f"The candidate must retain at least {minimum_retention:.0%} of active simulated profit."
            )
            st.session_state["flash_level"] = "warning"
        else:
            st.session_state["flash_message"] = f"Cycle {status}: {result.cycle.id}"
            st.session_state["flash_level"] = "error"
        st.rerun()
    except (ValueError, sqlite3.Error, RuntimeError) as error:
        st.error(f"Cycle did not complete: {error}")
