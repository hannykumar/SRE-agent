from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ui.common import (
    clear_run_state,
    current_result,
    current_status_label,
    current_execution_behavior,
    ensure_session_state,
    inject_app_styles,
    platform_overview,
    refresh_api_health,
    render_page_explainer,
    render_page_header,
    render_sidebar,
    render_status_pills,
    render_workflow_strip,
    result_summary_rows,
    run_selected_incident,
    runtime_health,
    workflow_active_step,
)


st.set_page_config(page_title="Workflow", layout="wide")

ensure_session_state()
inject_app_styles()
render_sidebar(show_run_controls=True)

result = current_result()
status_label = current_status_label(result)
health = refresh_api_health() or {}
platform = {}
try:
    platform = platform_overview()
except Exception:
    platform = {}

render_page_header(
    "Workflow",
    "Run the core SRE workflow: investigate an incident, review evidence, approve a safe action, and verify whether the signal recovered.",
    chips=[
        f"status={status_label}",
        f"execution={current_execution_behavior()}",
        "transport=mcp",
        f"planner={((health.get('runtime_profile') or {}).get('planner_provider') or 'deterministic')}",
    ],
)
render_workflow_strip(workflow_active_step(result))
render_page_explainer(
    [
        "This page is the control center for the investigation workflow.",
        "The model is only used for investigation planning.",
        "The rest of the system stays deterministic for retrieval, evidence handling, approval, execution, and verification.",
    ]
)

left_col, right_col = st.columns([1.45, 1], gap="large")

with left_col:
    with st.container(border=True):
        st.subheader("Current run")
        if result:
            metrics = st.columns(len(result_summary_rows(result)))
            for idx, (label, value) in enumerate(result_summary_rows(result)):
                metrics[idx].metric(label, value)

            st.markdown((result.get("final_response") or "").replace("\n", "  \n"))

            row = st.columns(4)
            row[0].page_link("pages/2_Investigation.py", label="Open Investigation")
            if result.get("requires_human_approval"):
                row[1].page_link("pages/3_Approval.py", label="Open Approval")
            if result.get("execution_results"):
                row[2].page_link("pages/4_Verification.py", label="Open Verification")
            row[3].page_link("pages/9_Run_History.py", label="Run History")
        else:
            st.info("No run is loaded. Start from the sidebar or Alert Lab.")
            if st.button("Run selected incident now", type="primary", use_container_width=True):
                with st.spinner("Running investigation..."):
                    run_selected_incident()
                st.rerun()

    if result:
        with st.container(border=True):
            st.subheader("Core evidence")
            tabs = st.tabs(["Top diagnosis", "Retrieval", "Action"])
            with tabs[0]:
                for item in result.get("candidate_diagnoses", [])[:3]:
                    st.write(
                        f"**{item.get('incident_type', 'Unknown')}** "
                        f"score={float(item.get('score', item.get('retrieval_confidence', 0.0))):.2f}"
                    )
                    if item.get("rationale"):
                        for rationale in item.get("rationale", [])[:3]:
                            st.write(f"- {rationale}")
            with tabs[1]:
                st.json(result.get("retrieval_quality", {}))
                explanation = result.get("retrieval_explanation", {})
                if explanation:
                    st.caption("Retrieval explanation")
                    st.json(explanation)
            with tabs[2]:
                if result.get("proposed_action"):
                    st.json(result.get("proposed_action", {}))
                    if result.get("planned_commands"):
                        st.code("\n".join(result.get("planned_commands", [])), language="bash")
                else:
                    st.caption("No remediation proposal is attached to this run yet.")

with right_col:
    with st.container(border=True):
        st.subheader("System focus")
        render_status_pills(
            [
                "planner=model-backed",
                "retrieval=guardrailed",
                "execution=human-approved",
                "verification=deterministic",
            ]
        )
        st.caption(
            "The active product path is now focused on stronger investigation quality. The model is reserved for planning only."
        )

    with st.container(border=True):
        st.subheader("Runtime health")
        runtime = {}
        try:
            runtime = runtime_health(active_probe=False)
        except Exception:
            runtime = {}
        planner = dict(runtime.get("planner") or {})
        st.write(f"Planner runtime: `{planner.get('status', 'unknown')}`")
        if planner.get("last_error"):
            st.caption(planner.get("last_error"))
        live = dict(runtime.get("live_backends") or {})
        st.write(f"Live backends ready: `{live.get('ready', False)}`")
        components = dict(live.get("components") or {})
        for name, item in components.items():
            st.caption(f"{name}: {item.get('status', 'unknown')} - {item.get('detail', '')}")

    with st.container(border=True):
        st.subheader("Platform snapshot")
        if platform:
            st.write(f"Integrations: `{len(platform.get('integrations', []))}`")
            st.write(f"Alert sources: `{len(platform.get('alert_sources', []))}`")
            st.write(f"Infrastructure memory records: `{platform.get('infrastructure_memory_count', 0)}`")
        st.page_link("pages/1_Alert_Lab.py", label="Open Alert Lab")
        st.caption("Use Advanced in the sidebar for platform, evaluation, and history pages.")
