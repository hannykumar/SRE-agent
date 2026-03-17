from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ui.common import (
    ensure_session_state,
    inject_app_styles,
    list_alert_sources,
    list_infrastructure_memory,
    list_integrations,
    platform_overview,
    runtime_health,
    render_breadcrumb,
    render_page_header,
    render_sidebar,
    render_status_pills,
)


st.set_page_config(page_title="Platform Overview", layout="wide")

ensure_session_state()
inject_app_styles()
render_sidebar(show_run_controls=False)

overview = platform_overview()
integrations = overview.get("integrations", []) or list_integrations().get("items", [])
alert_sources = overview.get("alert_sources", []) or list_alert_sources().get("items", [])
memory_items = list_infrastructure_memory(limit=20).get("items", [])
runtime = overview.get("runtime_health", {}) or runtime_health(active_probe=False)

render_breadcrumb(detail="Product-facing summary of connectors, alert adapters, and infrastructure memory.")
render_page_header(
    "Platform Overview",
    "Review the assistant platform surfaces: alert ingestion, integrations, and infrastructure memory.",
    eyebrow="Product Surface",
    chips=[
        f"integrations={len(integrations)}",
        f"alert sources={len(alert_sources)}",
        f"infrastructure memory={overview.get('infrastructure_memory_count', len(memory_items))}",
        f"model={dict(runtime.get('planner') or {}).get('status', 'unknown')}",
    ],
)

render_status_pills(
    [
        f"product={overview.get('product_name', 'AI Assistant for SRE')}",
        f"type={overview.get('assistant_type', 'hybrid_guarded_assistant')}",
        f"healthy integrations={overview.get('healthy_integrations', 0)}",
    ]
)

top_left, top_right = st.columns([1.2, 1], gap="large")

with top_left:
    with st.container(border=True):
        st.subheader("Alert sources")
        for item in alert_sources:
            st.write(f"**{item.get('name', '')}**")
            st.caption(item.get("description", ""))
            st.caption(f"type={item.get('source_type', '')} | ingest={item.get('ingest_path', '')}")

    with st.container(border=True):
        st.subheader("Integrations")
        if not integrations:
            st.caption("No integrations are registered.")
        else:
            for item in integrations:
                st.write(f"**{item.get('name', '')}**")
                st.caption(
                    " | ".join(
                        [
                            f"type={item.get('integration_type', '')}",
                            f"health={item.get('health_status', 'unknown')}",
                            f"safety={item.get('safety_level', 'unknown')}",
                            f"confirmation={item.get('confirmation_mode', 'auto')}",
                        ]
                    )
                )

with top_right:
    with st.container(border=True):
        st.subheader("Infrastructure memory")
        if not memory_items:
            st.caption("No infrastructure memory records are available.")
        else:
            for item in memory_items[:8]:
                st.write(f"**{item.get('service', '')}**")
                st.caption(item.get("summary", ""))

    with st.container(border=True):
        st.subheader("Manual confirmation posture")
        manual = overview.get("manual_confirmation_integrations", [])
        if manual:
            st.write("These integrations currently require manual confirmation:")
            for integration_id in manual:
                st.write(f"- `{integration_id}`")
        else:
            st.caption("No integrations currently require manual confirmation.")

    with st.container(border=True):
        st.subheader("Runtime health")
        planner = dict(runtime.get("planner") or {})
        st.write(f"Planner: `{planner.get('status', 'unknown')}`")
        if planner.get("detail") or planner.get("last_error"):
            st.caption(planner.get("detail") or planner.get("last_error"))
        live = dict(runtime.get("live_backends") or {})
        st.write(f"Live backend ready: `{live.get('ready', False)}`")
        for component, payload in dict(live.get("components") or {}).items():
            st.caption(f"{component}: {payload.get('status', 'unknown')} | {payload.get('detail', '')}")
