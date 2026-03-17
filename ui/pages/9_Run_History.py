from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ui.common import (
    current_run_id,
    ensure_session_state,
    execute_current_rollback,
    get_run_audit,
    get_run_rollback,
    inject_app_styles,
    list_runs,
    render_page_explainer,
    render_page_header,
    render_sidebar,
)


st.set_page_config(page_title="Run History", layout="wide")

ensure_session_state()
inject_app_styles()
render_sidebar(show_run_controls=False)

render_page_header("Run History", "Browse persisted runs, approvals, audits, and rollback records from the API and Postgres-backed storage.", eyebrow="Detail View", chips=["Postgres-backed", "Audit trail", "Rollback records"] )
render_page_explainer(
    [
        "This page shows the stored history for runs, approvals, executions, and rollback records.",
        "Use it to inspect what was persisted to Postgres after each workflow step.",
        "It is mainly for audit and debugging, not for the main operator flow.",
    ]
)

with st.expander("How This Maps To Postgres / pgAdmin"):
    st.write("Open `Servers -> SRE Agent Postgres -> Databases -> sre_agent -> Schemas -> public -> Tables` in pgAdmin.")
    st.write("The main tables are:")
    st.write("- `runs`: one row per investigation run, including the stored request, plan result, execute result, and status")
    st.write("- `audit_events`: append-only event log for run creation, approvals, execution, rollback, and failures")
    st.write("- `approval_records`: who approved what and when")
    st.write("- `execution_records`: what the executor actually ran or previewed")
    st.write("- `rollback_records`: rollback command and rollback payload for each run")
    st.write("If pgAdmin looks empty, expand `public -> Tables` and click refresh once.")

controls = st.columns([1, 1, 2])
limit = controls[0].selectbox("Recent runs", [10, 20, 50], index=1)
load_current = controls[1].button("Reload current run", type="primary")
controls[2].caption("Use an admin token here if you want the rollback execute button to work.")

payload = list_runs(limit=limit)
runs = payload.get("runs", [])
st.dataframe(runs, use_container_width=True)

run_id = current_run_id()
if not run_id:
    st.info("Run an incident first to inspect its audit and rollback history.")
    st.stop()

if load_current:
    st.rerun()

audit_col, rollback_col = st.columns(2)

with audit_col.container(border=True):
    st.subheader("Audit trail")
    try:
        audit = get_run_audit(run_id).get("events", [])
        if audit:
            st.dataframe(audit, use_container_width=True)
        else:
            st.caption("No audit events found.")
    except Exception as exc:
        st.error(str(exc))

with rollback_col.container(border=True):
    st.subheader("Rollback record")
    try:
        rollback = get_run_rollback(run_id).get("rollback", {})
        if rollback:
            st.json(rollback)
            if st.button("Execute rollback", use_container_width=True):
                with st.spinner("Executing rollback..."):
                    result = execute_current_rollback()
                st.success("Rollback request completed.")
                st.json(result.get("rollback_result", {}))
        else:
            st.caption("No rollback record found.")
    except Exception as exc:
        st.error(str(exc))
