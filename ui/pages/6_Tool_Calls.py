from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ui.common import ensure_session_state, inject_app_styles, render_page_explainer, render_page_header, render_sidebar, require_result, tool_trace_events


st.set_page_config(page_title="Tool Calls", layout="wide")

ensure_session_state()
inject_app_styles()
render_sidebar(show_run_controls=False)

render_page_header("Tool Calls", "Inspect the MCP inputs, observations, and execution events without the reasoning layer mixed in.", eyebrow="Detail View", chips=["MCP events", "Observations", "Execution record"] )
render_page_explainer(
    [
        "This page shows what the MCP tools returned to the agent.",
        "Use it to verify the raw observations behind the diagnosis and remediation.",
        "It separates tool data from the reasoning summary so debugging is clearer.",
    ]
)

result = require_result()
events = tool_trace_events(result)

st.metric("Tool events", len(events))

for idx, event in enumerate(events, 1):
    event_type = event.get("type", "trace")
    title = event.get("tool_name", event_type).replace("_", " ")

    with st.container(border=True):
        st.subheader(f"{idx}. {title.title()}")
        if event_type == "tool_observation":
            left, right = st.columns([1, 1.3])
            left.write("Tool args")
            left.json(event.get("tool_args", {}))
            right.write("Observation")
            right.code(str(event.get("observation", "")), language="text")
        elif event_type == "execute":
            st.write(f"Execution mode: `{event.get('mode', '-')}`")
            st.json(event.get("result", {}))
            if event.get("improvement"):
                st.write("Improvement check")
                st.json(event["improvement"])
        else:
            st.json(event)
