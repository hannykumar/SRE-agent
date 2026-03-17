from __future__ import annotations

import json
import os
import time
from base64 import b64encode
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote
from urllib import error, request

import streamlit as st
from lab.scenarios import ALERT_LAB_SCENARIOS


INCIDENTS = ["INC-001", "INC-002", "INC-003", "INC-004"]
SUPPORTED_EXECUTION_BEHAVIORS = ["preview", "live"]
PRODUCT_EXECUTION_BEHAVIOR = "preview"
PRODUCT_TOOL_TRANSPORT = "mcp"
OPS_API_URL = os.getenv("SRE_OPS_API_URL", "http://127.0.0.1:8090").strip()
OPS_API_TIMEOUT_SECONDS = float(os.getenv("SRE_OPS_API_TIMEOUT_SECONDS", "30").strip())
OPS_APPROVE_TIMEOUT_SECONDS = float(os.getenv("SRE_OPS_APPROVE_TIMEOUT_SECONDS", "180").strip())
OPS_PLAN_TIMEOUT_SECONDS = float(os.getenv("SRE_OPS_PLAN_TIMEOUT_SECONDS", "60").strip())
OPS_POLL_TIMEOUT_SECONDS = float(os.getenv("SRE_OPS_POLL_TIMEOUT_SECONDS", "5").strip())
OPS_POLL_INTERVAL_SECONDS = float(os.getenv("SRE_OPS_POLL_INTERVAL_SECONDS", "2").strip())
PGADMIN_URL = os.getenv("SRE_PGADMIN_URL", "http://127.0.0.1:5050").strip()
REDISINSIGHT_URL = os.getenv("SRE_REDISINSIGHT_URL", "http://127.0.0.1:5540").strip()
GRAFANA_URL = os.getenv("SRE_GRAFANA_URL", "http://127.0.0.1:3000").strip()
PROMETHEUS_URL = os.getenv("SRE_PROMETHEUS_URL", "http://127.0.0.1:9090").strip()
DEMO_SERVICE_URL = os.getenv("SRE_DEMO_SERVICE_URL", "http://127.0.0.1:8088").strip()
DEMO_SERVICE_API_URL = os.getenv("SRE_DEMO_SERVICE_API_URL", DEMO_SERVICE_URL).strip()
GRAFANA_API_URL = os.getenv("SRE_GRAFANA_API_URL", GRAFANA_URL).strip()
PROMETHEUS_API_URL = os.getenv("SRE_PROMETHEUS_API_URL", PROMETHEUS_URL).strip()
GRAFANA_USERNAME = os.getenv("SRE_GRAFANA_USERNAME", "admin").strip()
GRAFANA_PASSWORD = os.getenv("SRE_GRAFANA_PASSWORD", "admin").strip()
DEFAULT_ALERT_LAB_INCIDENT = "INC-002"
PROM_5XX_RATIO_QUERY = (
    'sum(rate(demo_http_requests_total{path="/",status=~"5.."}[1m])) '
    '/ clamp_min(sum(rate(demo_http_requests_total{path="/"}[1m])), 0.001)'
)
WORKFLOW_STEPS = [
    ("Alert Lab", "Trigger a Grafana incident and load the run."),
    ("Investigation", "Review the diagnosis, evidence, and runbooks."),
    ("Approval", "Choose preview or live and approve the remediation."),
    ("Verification", "Confirm whether the signal returned to normal."),
]


def default_api_token() -> str:
    return os.getenv("SRE_DEFAULT_API_TOKEN", "").strip()


def ops_api_url() -> str:
    return OPS_API_URL or "http://127.0.0.1:8090"


def session_value(key: str, default: Any = None) -> Any:
    try:
        return st.session_state.get(key, default)
    except Exception:
        return default


def ensure_session_state() -> None:
    defaults = {
        "plan_result": None,
        "execute_result": None,
        "run_request": None,
        "run_id": None,
        "selected_incident": INCIDENTS[0],
        "execution_behavior": PRODUCT_EXECUTION_BEHAVIOR,
        "tool_transport": PRODUCT_TOOL_TRANSPORT,
        "evaluation_result": None,
        "api_health": None,
        "alert_lab_incident": DEFAULT_ALERT_LAB_INCIDENT,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def inject_app_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
          --app-bg: #0b1020;
          --panel-bg: #121a2b;
          --panel-soft: #0f1726;
          --border: rgba(148, 163, 184, 0.18);
          --text: #e5e7eb;
          --muted: #94a3b8;
          --accent: #6aa6ff;
          --success: #22c55e;
          --warn: #f59e0b;
        }
        [data-testid="stAppViewContainer"] {
          background: linear-gradient(180deg, #0b1020 0%, #0d1424 100%);
          color: var(--text);
        }
        [data-testid="stSidebar"] {
          background: #0f1726;
          border-right: 1px solid var(--border);
        }
        [data-testid="stSidebar"] * {
          color: var(--text);
        }
        [data-testid="stSidebarNav"] {
          display: none;
        }
        [data-testid="stHeader"] {
          background: rgba(11, 16, 32, 0.85);
        }
        .block-container {
          padding-top: 1.6rem;
          padding-bottom: 2rem;
          max-width: 1500px;
        }
        div[data-testid="stVerticalBlockBorderWrapper"] > div {
          background: rgba(18, 26, 43, 0.88);
          border: 1px solid var(--border);
          border-radius: 16px;
          box-shadow: none;
        }
        div[data-testid="stMetric"] {
          background: rgba(15, 23, 38, 0.7);
          border: 1px solid var(--border);
          border-radius: 14px;
          padding: 0.75rem 0.85rem;
        }
        .sre-subtle {
          color: var(--muted);
          font-size: 0.92rem;
        }
        .sre-pills {
          display: flex;
          flex-wrap: wrap;
          gap: 0.5rem;
          margin: 0.25rem 0 1rem 0;
        }
        .sre-pill {
          display: inline-flex;
          align-items: center;
          gap: 0.35rem;
          padding: 0.35rem 0.7rem;
          border-radius: 999px;
          border: 1px solid var(--border);
          background: rgba(15, 23, 38, 0.9);
          color: var(--text);
          font-size: 0.82rem;
          white-space: nowrap;
        }
        .sre-page-top {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 1rem;
          margin-bottom: 0.6rem;
        }
        .sre-breadcrumb a {
          color: var(--accent);
          text-decoration: none;
          font-size: 0.9rem;
        }
        .sre-muted-title {
          color: var(--muted);
          letter-spacing: 0.04em;
          text-transform: uppercase;
          font-size: 0.75rem;
          margin-bottom: 0.25rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_page_header(title: str, intro: str, *, eyebrow: str = "AI Assistant for SRE", chips: List[str] | None = None) -> None:
    st.markdown(f"<div class='sre-muted-title'>{eyebrow}</div>", unsafe_allow_html=True)
    st.title(title)
    st.markdown(f"<div class='sre-subtle'>{intro}</div>", unsafe_allow_html=True)
    if chips:
        render_status_pills(chips)


def render_status_pills(items: List[str]) -> None:
    if not items:
        return
    pills = "".join(f"<span class='sre-pill'>{item}</span>" for item in items if str(item).strip())
    st.markdown(f"<div class='sre-pills'>{pills}</div>", unsafe_allow_html=True)


def render_breadcrumb(label: str = "Back to Workflow", *, target: str = "app.py", detail: str = "") -> None:
    left, right = st.columns([1, 2.5])
    left.page_link(target, label=label)
    if detail:
        right.caption(detail)


def render_workflow_strip(active_step: int) -> None:
    cols = st.columns(len(WORKFLOW_STEPS))
    for idx, ((title, description), col) in enumerate(zip(WORKFLOW_STEPS, cols), start=1):
        status = "Current" if idx == active_step else "Done" if idx < active_step else "Next"
        with col:
            with st.container(border=True):
                st.caption(f"Step {idx}")
                st.write(f"**{title}**")
                st.caption(description)
                st.caption(f"Status: {status}")


def render_page_explainer(lines: List[str], *, title: str = "What Happens On This Page") -> None:
    with st.container(border=True):
        st.subheader(title)
        for line in lines:
            st.write(f"- {line}")


def api_request(
    method: str,
    base_url: str,
    path: str,
    body: dict | None = None,
    token: str = "",
    timeout: float = OPS_API_TIMEOUT_SECONDS,
) -> dict:
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    headers = {"Content-Type": "application/json"}
    if token.strip():
        headers["X-API-Token"] = token.strip()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc


def json_request(
    method: str,
    url: str,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    basic_auth: tuple[str, str] | None = None,
    timeout: float = 10.0,
) -> Any:
    merged_headers = {"Content-Type": "application/json"}
    if headers:
        merged_headers.update(headers)
    if basic_auth:
        token = b64encode(f"{basic_auth[0]}:{basic_auth[1]}".encode("utf-8")).decode("ascii")
        merged_headers["Authorization"] = f"Basic {token}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(url=url, data=data, headers=merged_headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc


def api_post(base_url: str, path: str, body: dict, token: str = "", timeout: float = OPS_API_TIMEOUT_SECONDS) -> dict:
    return api_request("POST", base_url, path, body, token=token, timeout=timeout)


def api_get(base_url: str, path: str, token: str = "", timeout: float = OPS_API_TIMEOUT_SECONDS) -> dict:
    return api_request("GET", base_url, path, None, token=token, timeout=timeout)


def refresh_api_health() -> dict | None:
    try:
        payload = api_request("GET", ops_api_url(), "/health", token=default_api_token())
        st.session_state["api_health"] = payload
        return payload
    except Exception:
        st.session_state["api_health"] = None
        return None


def clear_run_state() -> None:
    st.session_state["plan_result"] = None
    st.session_state["execute_result"] = None
    st.session_state["run_request"] = None
    st.session_state["run_id"] = None


def apply_run_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    st.session_state["run_id"] = payload.get("run_id")
    st.session_state["plan_result"] = payload.get("plan_result")
    st.session_state["execute_result"] = payload.get("execute_result")
    st.session_state["run_request"] = payload.get("request")
    request_payload = payload.get("request") or {}
    st.session_state["execution_behavior"] = str(request_payload.get("execution_mode") or PRODUCT_EXECUTION_BEHAVIOR)
    return payload


def current_result() -> Dict[str, Any] | None:
    return session_value("execute_result") or session_value("plan_result")


def current_run_id() -> str:
    return str(session_value("run_id") or "")


def get_run_details(run_id: str | None = None) -> Dict[str, Any]:
    target = run_id or current_run_id()
    if not target:
        raise RuntimeError("No run is available.")
    return api_get(
        ops_api_url(),
        f"/runs/{target}",
        token=default_api_token(),
        timeout=OPS_POLL_TIMEOUT_SECONDS,
    )


def get_run_audit(run_id: str | None = None) -> Dict[str, Any]:
    target = run_id or current_run_id()
    if not target:
        raise RuntimeError("No run is available.")
    return api_get(
        ops_api_url(),
        f"/runs/{target}/audit",
        token=default_api_token(),
    )


def get_run_rollback(run_id: str | None = None) -> Dict[str, Any]:
    target = run_id or current_run_id()
    if not target:
        raise RuntimeError("No run is available.")
    return api_get(
        ops_api_url(),
        f"/runs/{target}/rollback",
        token=default_api_token(),
    )


def list_runs(limit: int = 20) -> Dict[str, Any]:
    return api_get(
        ops_api_url(),
        f"/runs?limit={int(limit)}",
        token=default_api_token(),
    )


def list_service_memory(limit: int = 20) -> Dict[str, Any]:
    return api_get(
        ops_api_url(),
        f"/service-memory?limit={int(limit)}",
        token=default_api_token(),
    )


def list_infrastructure_memory(limit: int = 20) -> Dict[str, Any]:
    return api_get(
        ops_api_url(),
        f"/infrastructure-memory?limit={int(limit)}",
        token=default_api_token(),
    )


def list_integrations() -> Dict[str, Any]:
    return api_get(
        ops_api_url(),
        "/integrations",
        token=default_api_token(),
    )


def list_alert_sources() -> Dict[str, Any]:
    return api_get(
        ops_api_url(),
        "/alert-sources",
        token=default_api_token(),
    )


def platform_overview() -> Dict[str, Any]:
    return api_get(
        ops_api_url(),
        "/platform/overview",
        token=default_api_token(),
    )


def runtime_health(active_probe: bool = False) -> Dict[str, Any]:
    suffix = "true" if active_probe else "false"
    return api_get(
        ops_api_url(),
        f"/platform/runtime-health?active_probe={suffix}",
        token=default_api_token(),
        timeout=OPS_POLL_TIMEOUT_SECONDS if active_probe else OPS_API_TIMEOUT_SECONDS,
    )


def latest_grafana_run(limit: int = 20, incident_id: str | None = None) -> Dict[str, Any] | None:
    runs = list_runs(limit=limit).get("runs", [])
    for run in runs:
        request_payload = run.get("request") or {}
        if str(request_payload.get("source", "")) != "grafana_webhook":
            continue
        if incident_id and str(request_payload.get("incident_id", "")) != incident_id:
            continue
        return run
    return None


def load_run_into_session(run_id: str) -> Dict[str, Any]:
    payload = get_run_details(run_id)
    return apply_run_payload(payload)


def wait_for_run_update(run_id: str, *, require_execute_result: bool, timeout: float) -> Dict[str, Any]:
    deadline = time.time() + timeout
    last_payload: Dict[str, Any] = {}
    while time.time() < deadline:
        payload = get_run_details(run_id)
        last_payload = payload
        if require_execute_result:
            if payload.get("execute_result") is not None:
                return apply_run_payload(payload)
            if str(payload.get("status", "")).lower() in {"execution_failed", "failed"}:
                return apply_run_payload(payload)
        else:
            if payload.get("plan_result") is not None:
                return apply_run_payload(payload)
            if str(payload.get("status", "")).lower() in {"planned", "failed"}:
                return apply_run_payload(payload)
        time.sleep(OPS_POLL_INTERVAL_SECONDS)
    if last_payload:
        apply_run_payload(last_payload)
    raise RuntimeError(f"Run `{run_id}` is still processing. Refresh the page and try again.")


def run_selected_incident() -> Dict[str, Any]:
    try:
        payload = api_post(
            ops_api_url(),
            "/runs/plan",
            {
                "incident_id": st.session_state["selected_incident"],
                "execution_mode": session_value("execution_behavior", PRODUCT_EXECUTION_BEHAVIOR),
            },
            token=default_api_token(),
            timeout=OPS_PLAN_TIMEOUT_SECONDS,
        )
        return apply_run_payload(payload)
    except RuntimeError as exc:
        if "timed out" not in str(exc).lower():
            raise
        time.sleep(OPS_POLL_INTERVAL_SECONDS)
        latest = None
        for run in list_runs(limit=30).get("runs", []):
            request_payload = run.get("request") or {}
            if str(request_payload.get("source", "")) != "ui":
                continue
            if str(request_payload.get("incident_id", "")) != str(st.session_state["selected_incident"]):
                continue
            latest = run
            break
        if not latest:
            raise
        return wait_for_run_update(str(latest.get("run_id", "")), require_execute_result=False, timeout=OPS_PLAN_TIMEOUT_SECONDS)


def demo_service_status() -> Dict[str, Any]:
    return json_request("GET", f"{DEMO_SERVICE_API_URL.rstrip('/')}/status", timeout=5)


def set_demo_service_mode(mode: str) -> Dict[str, Any]:
    return json_request("POST", f"{DEMO_SERVICE_API_URL.rstrip('/')}/admin/mode/{mode}", body={}, timeout=5)


def reset_demo_service() -> Dict[str, Any]:
    return json_request("POST", f"{DEMO_SERVICE_API_URL.rstrip('/')}/admin/reset", body={}, timeout=5)


def prometheus_5xx_ratio() -> float | None:
    query_url = f"{PROMETHEUS_API_URL.rstrip('/')}/api/v1/query?query={quote(PROM_5XX_RATIO_QUERY, safe='')}"
    payload = json_request("GET", query_url, timeout=5)
    values = (((payload or {}).get("data") or {}).get("result") or [])
    if not values:
        return None
    value = values[0].get("value", [None, None])[1]
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def prometheus_instant_value(query: str) -> float | None:
    query_url = f"{PROMETHEUS_API_URL.rstrip('/')}/api/v1/query?query={quote(query, safe='')}"
    payload = json_request("GET", query_url, timeout=5)
    values = (((payload or {}).get("data") or {}).get("result") or [])
    if not values:
        return None
    value = values[0].get("value", [None, None])[1]
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def scenario_signal_value(incident_id: str) -> float | None:
    scenario = ALERT_LAB_SCENARIOS.get(incident_id)
    if not scenario:
        return None
    return prometheus_instant_value(str(scenario["query"]))


def grafana_alert_state(alert_name: str) -> Dict[str, Any]:
    payload = json_request(
        "GET",
        f"{GRAFANA_API_URL.rstrip('/')}/api/prometheus/grafana/api/v1/alerts",
        basic_auth=(GRAFANA_USERNAME, GRAFANA_PASSWORD),
        timeout=5,
    )
    alerts = (((payload or {}).get("data") or {}).get("alerts") or [])
    for alert in alerts:
        labels = alert.get("labels") or {}
        if str(labels.get("alertname", "")) == alert_name:
            return alert
    return {}


def execute_current_rollback() -> Dict[str, Any]:
    run_id = current_run_id()
    if not run_id:
        raise RuntimeError("No run is available for rollback.")
    return api_post(
        ops_api_url(),
        f"/runs/{run_id}/rollback-execute",
        {},
        token=default_api_token(),
    )


def require_result() -> Dict[str, Any]:
    result = current_result()
    if not result:
        st.info("No run is loaded yet. Start from Alert Lab first.")
        st.page_link("pages/1_Alert_Lab.py", label="Go to Alert Lab")
        st.stop()
    return result


def current_status_label(result: Dict[str, Any] | None) -> str:
    if not result:
        return "Idle"
    if session_value("execute_result"):
        verification = result.get("verification") or {}
        if verification.get("enabled"):
            return "Resolved" if verification.get("resolved") else "Still firing"
        return "Executed live" if current_execution_behavior() == "live" else "Approved preview"
    if result.get("diagnosis") == "Unknown":
        return "Escalated"
    if result.get("requires_human_approval"):
        return "Pending approval"
    return "Complete"


def workflow_active_step(result: Dict[str, Any] | None) -> int:
    if not result:
        return 1
    if result.get("execution_results"):
        return 4
    if result.get("requires_human_approval"):
        return 3
    return 2


def current_execution_behavior() -> str:
    execute_result = session_value("execute_result") or {}
    if execute_result:
        selected = str(execute_result.get("execution_mode") or session_value("execution_behavior", PRODUCT_EXECUTION_BEHAVIOR))
        return "live" if selected == "live" else "preview"

    request_payload = session_value("run_request") or {}
    selected = str(session_value("execution_behavior", request_payload.get("execution_mode", PRODUCT_EXECUTION_BEHAVIOR)))
    return "live" if selected == "live" else "preview"


def render_sidebar(show_run_controls: bool = True) -> None:
    with st.sidebar:
        st.header("Navigation")

        if show_run_controls:
            st.subheader("Incident Run")
            st.selectbox("Incident", INCIDENTS, key="selected_incident")
            st.selectbox("Execution", SUPPORTED_EXECUTION_BEHAVIORS, key="execution_behavior")
            st.selectbox("Tool Transport", [PRODUCT_TOOL_TRANSPORT], key="tool_transport", disabled=True)
            st.caption("Transport stays fixed to `mcp`. Execution mode controls what happens after approval.")

            health = refresh_api_health()
            if health:
                st.caption(f"Storage: `{health.get('storage', '-')}`")
                st.caption(f"Executor: `{health.get('executor', '-')}`")
                st.caption(f"API: `{ops_api_url()}`")
                st.caption(f"MCP backend: `{health.get('mcp_backend', '-')}`")
                runtime_profile = health.get("runtime_profile", {})
                st.caption(f"Planner provider: `{runtime_profile.get('planner_provider', '-')}`")
                st.caption(f"Planner model: `{runtime_profile.get('planner_model', '-')}`")
                model_runtime = health.get("model_runtime", {})
                if model_runtime:
                    st.caption(
                        "Model runtime: "
                        f"{model_runtime.get('status', 'unknown')} "
                        f"(cooldown={model_runtime.get('cooldown_active', False)})"
                    )
                live_backend = health.get("live_backend", {})
                if live_backend.get("configured"):
                    components = dict(live_backend.get("components") or {})
                    st.caption(
                        "Live readiness: "
                        f"kubectl={components.get('kubectl', {}).get('status', 'unknown')} "
                        f"prometheus={components.get('prometheus', {}).get('status', 'unknown')} "
                        f"loki={components.get('loki', {}).get('status', 'unknown')}"
                    )
                if health.get("auth_enabled"):
                    st.caption("Auth: enabled")
                else:
                    st.caption("Auth: disabled")
            else:
                st.caption("API health is unavailable right now.")

            if session_value("execution_behavior", PRODUCT_EXECUTION_BEHAVIOR) == "live":
                st.warning("`live` will ask the executor to run the approved remediation against configured cluster access. Use this only when the executor has kubeconfig/RBAC set up.")

            if st.button("Investigate incident", type="primary", use_container_width=True):
                with st.spinner("Running investigation..."):
                    run_selected_incident()
                st.rerun()

            if st.button("Clear current run", use_container_width=True):
                clear_run_state()
                st.rerun()

        result = current_result()
        st.page_link("app.py", label="Workflow")
        st.page_link("pages/1_Alert_Lab.py", label="Alert Lab")

        with st.expander("Advanced", expanded=False):
            if result:
                st.subheader("Workflow Pages")
                st.page_link("pages/2_Investigation.py", label="Investigation")
                st.page_link("pages/3_Approval.py", label="Approval")
                st.page_link("pages/4_Verification.py", label="Verification")

                st.subheader("Detail Views")
                st.page_link("pages/5_Reasoning_Trace.py", label="Reasoning Trace")
                st.page_link("pages/6_Tool_Calls.py", label="Tool Calls")
                st.page_link("pages/8_GitOps_Preview.py", label="GitOps Preview")
            else:
                st.caption("Load a run from Alert Lab to unlock Investigation, Approval, Verification, and trace pages.")

            st.subheader("Utility Pages")
            st.page_link("pages/10_Platform_Overview.py", label="Platform Overview")
            st.page_link("pages/7_Evaluation.py", label="Evaluation")
            st.page_link("pages/9_Run_History.py", label="Run History")

            st.subheader("Data Admin")
            st.markdown(f"- [pgAdmin]({PGADMIN_URL})")
            st.markdown(f"- [RedisInsight]({REDISINSIGHT_URL})")

            st.subheader("Observability Lab")
            st.markdown(f"- [Grafana]({GRAFANA_URL})")
            st.markdown(f"- [Prometheus]({PROMETHEUS_URL})")
            st.markdown(f"- [Demo Service]({DEMO_SERVICE_URL})")


def approve_current_run() -> None:
    run_id = session_value("run_id")
    if not run_id:
        raise RuntimeError("No run is available for approval.")
    try:
        payload = api_post(
            ops_api_url(),
            f"/runs/{run_id}/approve-execute",
            {"execution_mode": session_value("execution_behavior", PRODUCT_EXECUTION_BEHAVIOR)},
            token=default_api_token(),
            timeout=OPS_APPROVE_TIMEOUT_SECONDS,
        )
        apply_run_payload(payload)
        return
    except RuntimeError as exc:
        if "timed out" not in str(exc).lower():
            raise
        wait_for_run_update(run_id, require_execute_result=True, timeout=OPS_APPROVE_TIMEOUT_SECONDS)


def gitops_preview_from_result(result: Dict[str, Any]) -> Dict[str, Any]:
    action = result.get("proposed_action") or {}
    if not str(action.get("execution_model", "")).startswith("gitops") and not str(action.get("action_type", "")).startswith("gitops_"):
        return {}

    manifest_path = str(action.get("manifest_path", ""))
    current_replicas = int(action.get("previous_replicas") or 1)
    desired_replicas = int(action.get("replicas") or current_replicas)
    manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": action.get("target", ""), "namespace": action.get("namespace", "")},
        "spec": {"replicas": desired_replicas},
    }
    return {
        "manifest_path": manifest_path,
        "current_replicas": current_replicas,
        "desired_replicas": desired_replicas,
        "preview_manifest": manifest,
        "rollback_command": (result.get("rollback_commands") or [""])[0],
    }


def read_gitops_manifest_if_present(result: Dict[str, Any]) -> Dict[str, Any] | None:
    preview = gitops_preview_from_result(result)
    manifest_path = str(preview.get("manifest_path", "")).strip()
    if not manifest_path:
        return None

    candidates = [
        Path("data/gitops_repo") / manifest_path,
        Path(manifest_path),
    ]
    for candidate in candidates:
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def agent_trace_events(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [event for event in result.get("trace", []) if event.get("type") in {"ingest", "retrieval", "agent_decision", "hitl_interrupt"}]


def tool_trace_events(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [event for event in result.get("trace", []) if event.get("type") in {"tool_observation", "execute"}]


def result_summary_rows(result: Dict[str, Any]) -> List[tuple[str, str]]:
    rows = [
        ("Incident", str(result.get("incident_id", "-"))),
        ("Diagnosis", str(result.get("diagnosis", "Unknown"))),
        ("Confidence", f"{float(result.get('confidence', 0.0)):.2f}"),
        ("Status", current_status_label(result)),
        ("Transport", PRODUCT_TOOL_TRANSPORT),
        ("Execution", current_execution_behavior()),
        ("Planner", str(result.get("planner_backend", "-"))),
    ]
    verification = result.get("verification") or {}
    if verification.get("enabled"):
        rows.append(("Alert", "Resolved" if verification.get("resolved") else "Still firing"))
    return rows
