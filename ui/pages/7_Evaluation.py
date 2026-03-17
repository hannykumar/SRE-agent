from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from evaluate_langgraph import INCIDENTS, run_evaluation
from ui.common import ensure_session_state, inject_app_styles, render_page_explainer, render_page_header, render_sidebar


st.set_page_config(page_title="Evaluation", layout="wide")

ensure_session_state()
inject_app_styles()
render_sidebar(show_run_controls=False)

render_page_header("Agent Evaluation", "Run the built-in benchmark against the product profile and compare retrieval, diagnosis, and verification outcomes.", eyebrow="Detail View", chips=["Benchmark", "Preview profile", "Offline evaluation"] )
render_page_explainer(
    [
        "This page runs the built-in benchmark across the demo incidents.",
        "Use it to compare diagnosis quality, retrieval quality, and verification outcomes.",
        "It is an offline evaluation view, not part of the main live workflow.",
    ]
)

if st.button("Run benchmark", type="primary", use_container_width=True):
    with st.spinner("Running evaluation across demo incidents..."):
        st.session_state["evaluation_result"] = run_evaluation(
            incident_ids=INCIDENTS,
            tool_mode="mcp",
            execution_mode="preview",
            save_outputs=True,
        )

payload = st.session_state.get("evaluation_result")
if not payload:
    st.info("Run the benchmark to populate aggregate metrics and incident-level results.")
    st.stop()

aggregates = payload.get("aggregates", {})
metric_keys = [
    "type_accuracy",
    "retrieval_hit@3",
    "retrieval_hit@5",
    "verification_pass",
    "improved_rate",
    "unsafe_execution_rate",
]
metric_cols = st.columns(len(metric_keys))
for idx, key in enumerate(metric_keys):
    metric_cols[idx].metric(key, f"{float(aggregates.get(key, 0.0)):.2f}")

with st.container(border=True):
    st.subheader("Benchmark settings")
    st.write(f"Tool transport: `{payload.get('tool_mode', '-')}`")
    st.write(f"Execution behavior: `{payload.get('execution_mode', '-')}`")
    if payload.get("latest_path"):
        st.write(f"Latest results: `{payload['latest_path']}`")
    if payload.get("history_path"):
        st.write(f"History snapshot: `{payload['history_path']}`")

table_rows = []
for row in payload.get("rows", []):
    table_rows.append(
        {
            "incident": row.get("incident"),
            "expected": row.get("expected"),
            "predicted": row.get("predicted"),
            "confirmed": row.get("confirmed"),
            "verification_pass": row.get("verification_pass"),
            "executed": row.get("executed"),
            "improved": row.get("improved"),
            "unsafe_execution": row.get("unsafe_execution"),
        }
    )

st.subheader("Per-incident results")
st.dataframe(table_rows, use_container_width=True)

st.subheader("Detailed benchmark records")
for row in payload.get("rows", []):
    with st.expander(f"{row.get('incident')} :: predicted={row.get('predicted')}"):
        st.json(row)
