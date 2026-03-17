from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ui.common import (
    ensure_session_state,
    inject_app_styles,
    render_breadcrumb,
    render_page_header,
    render_sidebar,
    render_status_pills,
    runtime_health,
    require_result,
)


st.set_page_config(page_title="Investigation", layout="wide")

ensure_session_state()
inject_app_styles()
render_sidebar(show_run_controls=False)

result = require_result()

render_breadcrumb(detail="Detailed evidence and diagnosis review")
render_page_header(
    "Investigation",
    "Review the diagnosis, evidence, coordinator summary, and runbook matches before deciding whether the proposed remediation is grounded.",
    chips=[
        f"diagnosis={result.get('diagnosis', 'Unknown')}",
        f"confidence={float(result.get('confidence', 0.0)):.2f}",
        f"incident={result.get('incident_id', '-')}",
    ],
)

top_left, top_right = st.columns([1.45, 0.95], gap="large")

with top_left.container(border=True):
    st.subheader("Diagnosis summary")
    st.markdown((result.get("final_response") or "").replace("\n", "  \n"))
    if result.get("candidate_diagnoses"):
        render_status_pills(
            [
                f"{item.get('incident_type', 'Unknown')} {float(item.get('score', item.get('retrieval_confidence', 0.0))):.2f}"
                for item in result.get("candidate_diagnoses", [])[:3]
            ]
        )

with top_right.container(border=True):
    st.subheader("Next step")
    if result.get("requires_human_approval"):
        st.page_link("pages/3_Approval.py", label="Open Approval")
    elif result.get("diagnosis") == "Unknown":
        st.page_link("pages/9_Run_History.py", label="Open Run History")
    else:
        st.page_link("pages/4_Verification.py", label="Open Verification")
    st.page_link("pages/5_Reasoning_Trace.py", label="Reasoning Trace")
    st.page_link("pages/6_Tool_Calls.py", label="Tool Calls")

main_left, main_right = st.columns([1.55, 1], gap="large")

with main_left:
    tabs = st.tabs(["Evidence", "Runbooks", "Ranking", "Action"])

    with tabs[0]:
        st.json(result.get("evidence", {}))

    with tabs[1]:
        if result.get("top_chunks"):
            for idx, chunk in enumerate(result.get("top_chunks", []), 1):
                with st.expander(f"{idx}. {chunk.get('source_file')} :: {chunk.get('section')}", expanded=(idx == 1)):
                    st.caption(f"score={float(chunk.get('score', 0.0)):.3f}")
                    st.code((chunk.get("text") or "")[:1800], language="markdown")
        else:
            st.caption("No runbook context was retrieved.")

    with tabs[2]:
        candidates = result.get("candidate_diagnoses", []) or []
        if candidates:
            for candidate in candidates[:5]:
                st.write(
                    f"**{candidate.get('incident_type', 'Unknown')}** "
                    f"score={float(candidate.get('score', candidate.get('retrieval_confidence', 0.0))):.2f}"
                )
                if candidate.get("rationale"):
                    st.caption(" | ".join(candidate.get("rationale", [])))
                if candidate.get("supporting_points"):
                    st.caption("Supporting signals")
                    for item in candidate.get("supporting_points", [])[:3]:
                        st.write(f"- {item}")
                if candidate.get("contradicting_signals"):
                    st.caption("Weak or contradicting signals")
                    for item in candidate.get("contradicting_signals", [])[:2]:
                        st.write(f"- {item}")
                if candidate.get("citations"):
                    st.caption(f"Citations: {', '.join(candidate.get('citations', []))}")
        else:
            st.caption("No ranked candidates were recorded for this run.")

    with tabs[3]:
        if result.get("proposed_action"):
            left, right = st.columns(2)
            left.json(result.get("proposed_action", {}))
            right.write("Planned command")
            if result.get("planned_commands"):
                right.code("\n".join(result.get("planned_commands", [])), language="bash")
            if result.get("rollback_commands"):
                right.caption("Rollback")
                right.code("\n".join(result.get("rollback_commands", [])), language="bash")
        else:
            st.caption("No remediation proposal is attached to this run.")

with main_right:
    with st.container(border=True):
        st.subheader("Retrieval")
        st.json(result.get("retrieval_quality", {}))
        explanation = result.get("retrieval_explanation", {})
        if explanation:
            st.caption("Retrieval explanation")
            st.json(explanation)

    with st.container(border=True):
        st.subheader("Infrastructure memory")
        st.json(result.get("infrastructure_memory") or result.get("service_memory", {}))

    with st.container(border=True):
        st.subheader("Coordinator and specialists")
        coordinator = result.get("coordinator_summary") or {}
        if coordinator:
            st.write(f"**Coordinator focus:** `{coordinator.get('focus', '-')}`")
            st.write(f"**Reliability:** `{coordinator.get('reliability', 'unknown')}`")
            st.caption(coordinator.get("summary", ""))
            if coordinator.get("recommended_next_tool"):
                st.caption(f"Recommended next tool: {coordinator.get('recommended_next_tool')}")
        if result.get("tool_confirmation_required"):
            st.warning(result.get("tool_confirmation_prompt", "Manual tool confirmation is required."))
        st.write("Specialist findings")
        findings = result.get("specialist_findings", []) or []
        if findings:
            for finding in findings:
                st.write(
                    f"**{finding.get('specialist', '')}** "
                    f"status=`{finding.get('status', 'unknown')}` "
                    f"confidence=`{float(finding.get('confidence', 0.0)):.2f}`"
                )
                st.caption(finding.get("summary", ""))
                if finding.get("recommended_next_tools"):
                    st.caption(f"Next tools: {', '.join(finding.get('recommended_next_tools', [])[:3])}")
        else:
            st.caption("No specialist findings were recorded.")

    with st.container(border=True):
        st.subheader("Investigation activity")
        activity = result.get("investigation_activity", []) or []
        if activity:
            for item in activity:
                st.write(f"**{item.get('stage', 'step')}** · {item.get('status', 'unknown')}")
                st.caption(item.get("summary", ""))
        else:
            st.caption("No investigation activity is available yet.")

    with st.container(border=True):
        st.subheader("Runtime reliability")
        runtime = runtime_health(active_probe=False)
        planner = runtime.get("planner", {})
        st.write(f"Planner runtime: `{planner.get('status', 'unknown')}`")
        if planner.get("last_error"):
            st.caption(planner.get("last_error"))
        live = runtime.get("live_backends", {})
        st.write(f"Live backend ready: `{live.get('ready', False)}`")
        for name, item in dict(live.get("components") or {}).items():
            st.caption(f"{name}: {item.get('status', 'unknown')} - {item.get('detail', '')}")
