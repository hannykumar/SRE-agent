from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ui.common import (
    clear_run_state,
    current_execution_behavior,
    demo_service_status,
    ensure_session_state,
    inject_app_styles,
    render_breadcrumb,
    render_page_header,
    render_sidebar,
    require_result,
)


st.set_page_config(page_title="Verification", layout="wide")

ensure_session_state()
inject_app_styles()
render_sidebar(show_run_controls=False)

result = require_result()
execute_result = result.get("execution_results") or []
verification = result.get("verification") or {}

render_breadcrumb(detail="Post-action verification and recovery status")
render_page_header(
    "Verification",
    "Check whether the approved remediation actually improved the monitored signal.",
    chips=[
        f"execution={current_execution_behavior()}",
        f"resolved={bool(verification.get('resolved', False))}",
        f"improved={bool(result.get('improved', False))}",
    ],
)

if not execute_result:
    st.info("This run has not been approved yet. Go to Approval first.")
    st.page_link("pages/3_Approval.py", label="Go to Approval")
    st.stop()

top_left, top_right = st.columns([1.25, 1], gap="large")

with top_left.container(border=True):
    st.subheader("Verification verdict")
    st.write(f"Execution mode: `{current_execution_behavior()}`")
    st.write(f"Improved: `{bool(result.get('improved', False))}`")
    if verification.get("enabled"):
        if verification.get("resolved"):
            st.success("The monitored signal returned to normal.")
        else:
            st.warning("The remediation completed, but the alert is still firing.")
    elif current_execution_behavior() == "preview":
        st.info("Preview mode does not apply changes, so the alert is expected to keep firing.")
    else:
        st.info("No alert verification payload is available for this run.")
    if result.get("improvement_summary"):
        st.caption(result.get("improvement_summary", ""))

with top_right.container(border=True):
    st.subheader("Next step")
    st.page_link("app.py", label="Back to Workflow")
    st.page_link("pages/1_Alert_Lab.py", label="Trigger another alert")
    st.page_link("pages/9_Run_History.py", label="Open Run History")
    if st.button("Reset workspace", use_container_width=True):
        clear_run_state()
        st.switch_page("pages/1_Alert_Lab.py")

body_left, body_right = st.columns([1.2, 1], gap="large")

with body_left:
    tabs = st.tabs(["Execution", "Verification", "Post-action evidence"])
    with tabs[0]:
        st.json(result.get("execution_results", []))
        if result.get("rollback_record"):
            st.caption("Rollback record")
            st.json(result.get("rollback_record", {}))
    with tabs[1]:
        if verification:
            st.json(verification)
        else:
            st.caption("No verification payload recorded.")
        try:
            status = demo_service_status()
            st.caption("Current demo service state")
            st.json(status)
        except Exception as exc:
            st.caption(f"Demo service status unavailable: {exc}")
    with tabs[2]:
        if result.get("evidence_after"):
            st.json(result.get("evidence_after", {}))
        else:
            st.caption("No post-action evidence was captured for this run.")

with body_right.container(border=True):
    st.subheader("Run summary")
    st.write(f"Diagnosis: `{result.get('diagnosis', 'Unknown')}`")
    st.write(f"Confidence: `{float(result.get('confidence', 0.0)):.2f}`")
    if result.get("proposed_action"):
        st.caption("Approved action")
        st.json(result.get("proposed_action", {}))
