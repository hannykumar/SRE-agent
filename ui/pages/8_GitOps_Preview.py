from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ui.common import ensure_session_state, gitops_preview_from_result, inject_app_styles, read_gitops_manifest_if_present, render_page_explainer, render_page_header, render_sidebar, require_result


st.set_page_config(page_title="GitOps Preview", layout="wide")

ensure_session_state()
inject_app_styles()
render_sidebar(show_run_controls=False)

render_page_header("GitOps Preview", "Inspect Git-managed remediation proposals and the paired rollback plan before treating them as a production change request.", eyebrow="Detail View", chips=["GitOps path", "Rollback ready", "Change preview"] )
render_page_explainer(
    [
        "This page is only for runs that use the GitOps remediation path.",
        "It shows the desired manifest change and the rollback plan before any real merge or apply step.",
        "Use it when you want to inspect configuration-oriented remediations more closely.",
    ]
)

result = require_result()
preview = gitops_preview_from_result(result)

if not preview:
    st.info("The current run does not use a GitOps remediation path.")
    st.stop()

top = st.columns(4)
top[0].metric("Manifest", preview.get("manifest_path", "-"))
top[1].metric("Current replicas", str(preview.get("current_replicas", "-")))
top[2].metric("Desired replicas", str(preview.get("desired_replicas", "-")))
top[3].metric("Action type", str((result.get("proposed_action") or {}).get("action_type", "-")))

left, right = st.columns(2)

with left.container(border=True):
    st.subheader("Desired manifest preview")
    st.json(preview.get("preview_manifest", {}))

with right.container(border=True):
    st.subheader("Current manifest on disk")
    existing = read_gitops_manifest_if_present(result)
    if existing:
        st.json(existing)
    else:
        st.caption("No local manifest exists yet for this preview path.")

with st.container(border=True):
    st.subheader("GitOps commands")
    if result.get("planned_commands"):
        st.code("\n".join(result["planned_commands"]), language="bash")
    if preview.get("rollback_command"):
        st.caption("Rollback command")
        st.code(str(preview["rollback_command"]), language="bash")

with st.container(border=True):
    st.subheader("Approval note")
    st.write(
        "This project stays in preview mode for remediation approval. "
        "For GitOps-managed workloads, approval confirms the proposed change path and rollback plan, "
        "not an immediate cluster mutation."
    )
