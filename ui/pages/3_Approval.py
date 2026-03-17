from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ui.common import (
    PRODUCT_EXECUTION_BEHAVIOR,
    SUPPORTED_EXECUTION_BEHAVIORS,
    approve_current_run,
    current_execution_behavior,
    ensure_session_state,
    inject_app_styles,
    render_breadcrumb,
    render_page_header,
    render_sidebar,
    require_result,
)


st.set_page_config(page_title="Approval", layout="wide")

ensure_session_state()
inject_app_styles()
render_sidebar(show_run_controls=False)

result = require_result()

render_breadcrumb(detail="Human approval gate before any remediation is executed")
render_page_header(
    "Approval",
    "Approve only when the evidence and remediation path look grounded and safe.",
    chips=[
        f"mode={current_execution_behavior()}",
        f"action={(result.get('proposed_action') or {}).get('action_type', 'none')}",
        f"incident={result.get('incident_id', '-')}",
    ],
)

if result.get("diagnosis") == "Unknown":
    st.warning("This run escalated to a Level 2 SRE engineer. There is no remediation to approve.")
    st.page_link("pages/2_Investigation.py", label="Back to Investigation")
    st.stop()

st.session_state["execution_behavior"] = st.radio(
    "Execution mode",
    SUPPORTED_EXECUTION_BEHAVIORS,
    horizontal=True,
    index=SUPPORTED_EXECUTION_BEHAVIORS.index(
        current_execution_behavior()
        if current_execution_behavior() in SUPPORTED_EXECUTION_BEHAVIORS
        else PRODUCT_EXECUTION_BEHAVIOR
    ),
)

if st.session_state["execution_behavior"] == "live":
    st.warning("Live approval waits for executor completion and alert verification. Expect a slower path.")
else:
    st.info("Preview records the remediation and command preview without mutating infrastructure.")

left, right = st.columns([1.2, 1], gap="large")

with left.container(border=True):
    st.subheader("Decision context")
    if result.get("approval_prompt"):
        st.info(result.get("approval_prompt", ""))
    st.write("Structured action")
    st.json(result.get("proposed_action", {}))
    if result.get("execution_results"):
        st.success("This run has already been approved.")
        st.page_link("pages/4_Verification.py", label="Open Verification")

with right.container(border=True):
    st.subheader("Command preview")
    if result.get("planned_commands"):
        st.code("\n".join(result.get("planned_commands", [])), language="bash")
    else:
        st.caption("No command preview is available.")
    if result.get("rollback_commands"):
        st.caption("Rollback")
        st.code("\n".join(result.get("rollback_commands", [])), language="bash")

actions = st.columns([1, 1, 1.2], gap="small")
approve = actions[0].button(
    "Approve remediation",
    type="primary",
    use_container_width=True,
    disabled=bool(result.get("execution_results")),
)
reject = actions[1].button(
    "Reject remediation",
    use_container_width=True,
    disabled=bool(result.get("execution_results")),
)
actions[2].page_link("pages/2_Investigation.py", label="Back to Investigation")

if approve:
    with st.spinner("Submitting approval and waiting for the run to update..."):
        approve_current_run()
    st.success("Approval finished.")
    st.page_link("pages/4_Verification.py", label="Continue to Verification")

if reject:
    st.warning("Remediation rejected. The run remains available for manual handoff.")
