from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ui.common import (
    ALERT_LAB_SCENARIOS,
    current_run_id,
    demo_service_status,
    ensure_session_state,
    inject_app_styles,
    get_run_details,
    grafana_alert_state,
    latest_grafana_run,
    load_run_into_session,
    render_page_explainer,
    render_page_header,
    render_sidebar,
    render_workflow_strip,
    reset_demo_service,
    scenario_signal_value,
    set_demo_service_mode,
)


st.set_page_config(page_title="Alert Lab", layout="wide")

ensure_session_state()
inject_app_styles()
render_sidebar(show_run_controls=False)

render_page_header(
    "Alert Lab",
    "Trigger one of the demo alert scenarios, wait for the Grafana adapter to create a run, then load that run into the workspace.",
    chips=["Step 1 of 4", "Alert Adapter Demo", "Three live demo scenarios"],
)
render_workflow_strip(1)
render_page_explainer(
    [
        "This page controls the demo service and waits for Grafana to fire a real alert.",
        "When the Grafana alert adapter sends the webhook, the API creates a run and the assistant starts planning automatically.",
        "Use the scenario cards to trigger the alert, then open the new run in Investigation.",
    ]
)

toolbar = st.columns([1, 1])
if toolbar[0].button("Reset Demo", use_container_width=True):
    with st.spinner("Resetting the demo service..."):
        payload = reset_demo_service()
    st.success(f"Demo service mode: {payload.get('mode', 'unknown')}")
    st.rerun()

if toolbar[1].button("Refresh Status", use_container_width=True):
    st.rerun()

status = {}
try:
    status = demo_service_status()
except Exception as exc:
    st.warning(f"Demo service status is unavailable: {exc}")

mode = str(status.get("mode", "unknown"))
signals = status.get("signals") or {}

st.write("How this page works:")
st.write("1. Pick a scenario card and click `Trigger alert`.")
st.write("2. Wait about 20 to 60 seconds for Prometheus and Grafana to fire.")
st.write("3. When the latest run appears, click `Open Investigation`.")
st.write("4. Continue to the `Investigation` page.")
if current_run_id():
    st.caption(f"Current workspace run: `{current_run_id()}`")
    st.page_link("pages/2_Investigation.py", label="Continue to Investigation")

cards = st.columns(3)

for column, (incident_id, scenario) in zip(cards, ALERT_LAB_SCENARIOS.items()):
    with column.container(border=True):
        st.subheader(f"{incident_id} · {scenario['title']}")
        st.caption(f"Mode: `{scenario['mode']}`")

        if st.button(f"Trigger alert for {incident_id}", key=f"trigger_{incident_id}", type="primary", use_container_width=True):
            with st.spinner(f"Turning demo service to {scenario['mode']} mode..."):
                payload = set_demo_service_mode(str(scenario["mode"]))
            st.success(f"Demo service mode: {payload.get('mode', 'unknown')}")
            st.rerun()

        is_active = bool(signals.get(incident_id))
        st.metric("Demo signal active", "yes" if is_active else "no")
        st.caption(f"Current demo mode: `{mode}`")

        try:
            signal_value = scenario_signal_value(incident_id)
            st.metric("Prometheus signal", "-" if signal_value is None else f"{signal_value:.2f}")
        except Exception as exc:
            st.metric("Prometheus signal", "unavailable")
            st.caption(str(exc))

        try:
            alert = grafana_alert_state(str(scenario["alertname"]))
            alert_state = str(alert.get("state", "Normal")) if alert else "Normal"
            st.metric("Grafana alert", alert_state)
            st.caption(str(scenario["alertname"]))
        except Exception as exc:
            st.metric("Grafana alert", "unavailable")
            st.caption(str(exc))

        latest = None
        latest_details = None
        try:
            latest = latest_grafana_run(limit=30, incident_id=incident_id)
            if latest:
                latest_details = get_run_details(str(latest.get("run_id", "")))
        except Exception as exc:
            st.caption(f"Run lookup failed: {exc}")

        if latest and latest_details:
            result = latest_details.get("execute_result") or latest_details.get("plan_result") or {}
            st.write(f"Latest run: `{latest_details.get('run_id', '-')}`")
            st.write(f"Diagnosis: `{result.get('diagnosis', '-')}`")
            st.write(f"Planner: `{result.get('planner_backend', '-')}`")
            if st.button(f"Open Investigation for {incident_id}", key=f"load_{incident_id}", use_container_width=True):
                load_run_into_session(str(latest_details.get("run_id", "")))
                st.switch_page("pages/2_Investigation.py")
        else:
            st.info("No Grafana-triggered run for this scenario yet.")

with st.expander("Behind The Scenes"):
    st.write("- The demo service exposes synthetic incident signals through Prometheus metrics.")
    st.write("- Grafana is acting as one alert-source adapter demo: CrashLoop/OOM, High 5xx, and DNS failure.")
    st.write("- When a rule fires, the Grafana adapter sends a webhook to the assistant API.")
    st.write("- The API maps the alert label `incident_id` directly to `INC-001`, `INC-002`, or `INC-003`.")
    st.write("- The assistant immediately starts a plan run through the guarded investigation workflow.")
