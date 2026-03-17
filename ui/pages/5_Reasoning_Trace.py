from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ui.common import agent_trace_events, ensure_session_state, inject_app_styles, render_page_explainer, render_page_header, render_sidebar, require_result


st.set_page_config(page_title="Reasoning Trace", layout="wide")

ensure_session_state()
inject_app_styles()
render_sidebar(show_run_controls=False)

render_page_header("Reasoning Trace", "Inspect the model-facing investigation trail without mixing it with raw tool payloads.", eyebrow="Detail View", chips=["Reasoning only", "Agent decisions", "Read-only trace"] )
render_page_explainer(
    [
        "This page shows the agent decisions step by step without the raw MCP payloads.",
        "Use it to see the hypothesis, confidence, and next action chosen by the agent.",
        "If you want the underlying observations, open Tool Calls.",
    ]
)

result = require_result()
events = agent_trace_events(result)

decision_events = [event for event in events if event.get("type") == "agent_decision"]
st.metric("Agent decisions", len(decision_events))

if result.get("top_chunks"):
    with st.container(border=True):
        st.subheader("Retrieved context")
        top_chunk = result["top_chunks"][0]
        st.write(f"Top runbook: `{top_chunk.get('source_file', '-')}`")
        st.write(f"Predicted incident type: `{result.get('predicted_type', 'Unknown')}`")

for idx, event in enumerate(events, 1):
    event_type = event.get("type", "trace")
    label = event_type.replace("_", " ").title()
    if event_type == "agent_decision":
        label = f"Step {event.get('step_id', idx)}"

    with st.container(border=True):
        st.subheader(label)
        if event_type == "agent_decision":
            cols = st.columns(3)
            cols[0].metric("Hypothesis", event.get("hypothesis", "Unknown"))
            cols[1].metric("Decision", event.get("decision", "-"))
            cols[2].metric("Confidence", f"{float(event.get('confidence_after', 0.0)):.2f}")
            st.write(event.get("thought", ""))
            if event.get("tool_name"):
                st.caption(f"Next action: {event['tool_name']}")
        elif event_type == "retrieval":
            st.write("Runbook retrieval finished. Use the overview page to inspect the full chunk text.")
        elif event_type == "ingest":
            st.write(f"Incident `{event.get('incident_id', '-')}` was loaded into the graph.")
        elif event_type == "hitl_interrupt":
            st.info(event.get("prompt", ""))
        else:
            st.json(event)
