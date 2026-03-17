from __future__ import annotations

import json
import time
from base64 import b64encode
from typing import Any, Dict, List
from urllib.parse import quote
from urllib.request import Request, urlopen

from executor.gitops import GitOpsExecutor
from agent.incident_catalog import incident_definition
from lab.scenarios import get_alert_lab_scenario, is_alert_lab_incident
from mcp_tools.actions import normalize_action, render_action_to_command, rollback_action_for_action, rollback_command_for_action
from mcp_tools.kubectl_client import KubectlMCPClient
from mcp_tools.tool_gateway import get_tool_client
from ops.settings import get_settings


def assess_improvement(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    before_metrics = before.get("metrics", {})
    after_metrics = after.get("metrics", {})
    before_logs = "\n".join(before.get("logs_tail", [])).lower()
    after_logs = "\n".join(after.get("logs_tail", [])).lower()

    improved = False
    notes: List[str] = []

    for key in ["error_rate_percent", "dns_error_rate_percent", "memory_percent", "p95_latency_ms"]:
        before_value = before_metrics.get(key)
        after_value = after_metrics.get(key)
        if isinstance(before_value, (int, float)) and isinstance(after_value, (int, float)):
            if after_value < before_value:
                improved = True
            notes.append(f"{key}: {before_value} -> {after_value}")

    tokens = ["503", "nxdomain", "could not resolve", "out of memory", "oom"]
    before_hits = sum(token in before_logs for token in tokens)
    after_hits = sum(token in after_logs for token in tokens)
    if after_hits < before_hits:
        improved = True
    notes.append(f"log_error_signals: {before_hits} -> {after_hits}")

    return {"improved": improved, "summary": "; ".join(notes)}


def assess_verification_rules(diagnosis: str, before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    definition = incident_definition(diagnosis)
    if definition is None:
        return {"enabled": False, "resolved": False, "reason": "no_catalog_verification_rules", "checks": []}

    before_metrics = before.get("metrics", {})
    after_metrics = after.get("metrics", {})
    after_logs = "\n".join(after.get("logs_tail", [])).lower()
    checks: List[Dict[str, Any]] = []
    passed_required = True
    passed_any = False

    for rule in definition.verification_rules:
        if "metric" in rule:
            metric = str(rule["metric"])
            direction = str(rule.get("direction", "decrease"))
            before_value = before_metrics.get(metric)
            after_value = after_metrics.get(metric)
            passed = False
            if isinstance(before_value, (int, float)) and isinstance(after_value, (int, float)):
                passed = after_value < before_value if direction == "decrease" else after_value > before_value
            checks.append(
                {
                    "type": "metric",
                    "metric": metric,
                    "direction": direction,
                    "before": before_value,
                    "after": after_value,
                    "passed": passed,
                    "optional": bool(rule.get("optional", False)),
                }
            )
        elif "log_absent" in rule:
            tokens = [str(token).lower() for token in rule.get("log_absent", [])]
            passed = all(token not in after_logs for token in tokens)
            checks.append(
                {
                    "type": "log_absent",
                    "tokens": tokens,
                    "passed": passed,
                    "optional": bool(rule.get("optional", False)),
                }
            )
        else:
            continue

        if checks[-1]["passed"]:
            passed_any = True
        if not checks[-1]["optional"] and not checks[-1]["passed"]:
            passed_required = False

    resolved = bool(checks) and passed_required and (passed_any or all(check.get("optional", False) for check in checks))
    return {"enabled": bool(checks), "resolved": resolved, "reason": diagnosis, "checks": checks}


def _json_request(
    method: str,
    url: str,
    *,
    body: Dict[str, Any] | None = None,
    headers: Dict[str, str] | None = None,
    basic_auth: tuple[str, str] | None = None,
    timeout: float = 5.0,
) -> Any:
    merged_headers = {"Content-Type": "application/json"}
    if headers:
        merged_headers.update(headers)
    if basic_auth:
        token = b64encode(f"{basic_auth[0]}:{basic_auth[1]}".encode("utf-8")).decode("ascii")
        merged_headers["Authorization"] = f"Basic {token}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(url=url, data=data, headers=merged_headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _reset_demo_service() -> Dict[str, Any]:
    settings = get_settings()
    base_url = settings.demo_service_api_url.rstrip("/")
    return _json_request("POST", f"{base_url}/admin/reset", body={}, timeout=5.0)


def _prometheus_value(query: str) -> float | None:
    settings = get_settings()
    base_url = settings.prometheus_api_url.rstrip("/")
    payload = _json_request("GET", f"{base_url}/api/v1/query?query={quote(query, safe='')}", timeout=5.0)
    values = (((payload or {}).get("data") or {}).get("result") or [])
    if not values:
        return None
    value = values[0].get("value", [None, None])[1]
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _grafana_alert_state(alert_name: str) -> str:
    settings = get_settings()
    base_url = settings.grafana_api_url.rstrip("/")
    payload = _json_request(
        "GET",
        f"{base_url}/api/prometheus/grafana/api/v1/alerts",
        basic_auth=(settings.grafana_username, settings.grafana_password),
        timeout=5.0,
    )
    alerts = (((payload or {}).get("data") or {}).get("alerts") or [])
    for alert in alerts:
        labels = alert.get("labels") or {}
        if str(labels.get("alertname", "")) == alert_name:
            return str(alert.get("state", "Normal"))
    return "Normal"


def verify_alert_resolution(incident_id: str) -> Dict[str, Any]:
    settings = get_settings()
    scenario = get_alert_lab_scenario(incident_id)
    if not scenario:
        return {"enabled": False, "resolved": False, "reason": "not_alert_lab_incident"}

    deadline = time.time() + settings.alert_verify_timeout_seconds
    alert_state = "Unknown"
    metric_value = None
    threshold = float(scenario.get("resolved_below", 0.0))

    while time.time() < deadline:
        metric_value = _prometheus_value(str(scenario["query"]))
        alert_state = _grafana_alert_state(str(scenario["alertname"]))
        metric_ok = metric_value is not None and metric_value < threshold
        alert_ok = alert_state.lower() not in {"alerting", "pending"}
        if metric_ok and alert_ok:
            return {
                "enabled": True,
                "resolved": True,
                "incident_id": incident_id,
                "alertname": scenario["alertname"],
                "alert_state": alert_state,
                "metric_value": metric_value,
                "metric_threshold": threshold,
            }
        time.sleep(settings.alert_verify_poll_seconds)

    return {
        "enabled": True,
        "resolved": False,
        "incident_id": incident_id,
        "alertname": scenario["alertname"],
        "alert_state": alert_state,
        "metric_value": metric_value,
        "metric_threshold": threshold,
    }


def execute_alert_lab_action(
    *,
    run_id: str,
    tool_mode: str,
    incident_id: str,
    incident: Dict[str, Any],
    action: Dict[str, Any],
    evidence_before: Dict[str, Any],
    planned_command: str,
    diagnosis: str = "Unknown",
) -> Dict[str, Any]:
    execution_results: List[Dict[str, Any]] = []

    if action["action_type"].startswith("gitops_"):
        execution_results.append(
            GitOpsExecutor().apply_action(run_id=run_id or incident_id, action=action, incident=incident)
        )

    execution_results.append(get_tool_client(tool_mode, incident_id, allow_write=True).execute_remediation(action=action))

    verification: Dict[str, Any]
    try:
        reset_payload = _reset_demo_service()
        execution_results.append(
            {
                "tool": "alert_lab_adapter",
                "status": "ok",
                "action": "reset_demo_service",
                "response": reset_payload,
            }
        )
        verification = verify_alert_resolution(incident_id)
    except Exception as exc:
        execution_results.append(
            {
                "tool": "alert_lab_adapter",
                "status": "error",
                "action": "reset_demo_service",
                "error": str(exc),
            }
        )
        verification = {
            "enabled": False,
            "resolved": False,
            "incident_id": incident_id,
            "reason": f"alert_lab_unavailable: {exc}",
        }

    after = collect_evidence(tool_mode, incident_id, incident)
    improvement = assess_improvement(evidence_before, after)
    catalog_verification = assess_verification_rules(diagnosis, evidence_before, after)
    improved = bool(improvement["improved"] or verification.get("resolved", False) or catalog_verification.get("resolved", False))

    summary_parts = [improvement["summary"]]
    if verification.get("enabled"):
        summary_parts.append(
            f"alert_resolution={verification.get('resolved', False)} "
            f"grafana_state={verification.get('alert_state', 'Unknown')} "
            f"signal={verification.get('metric_value')}"
        )

    merged_verification = {
        **catalog_verification,
        "enabled": bool(catalog_verification.get("enabled") or verification.get("enabled")),
        "resolved": bool(catalog_verification.get("resolved") or verification.get("resolved")),
        "external": verification,
        "alert_state": verification.get("alert_state", catalog_verification.get("alert_state", "")),
        "metric_value": verification.get("metric_value"),
        "metric_threshold": verification.get("metric_threshold"),
    }

    return {
        "status": "completed",
        "execution_results": execution_results,
        "evidence_after": after,
        "improved": improved,
        "improvement_summary": "; ".join(part for part in summary_parts if part),
        "command": planned_command,
        "verification": merged_verification,
    }


def collect_evidence(tool_mode: str, incident_id: str, incident: Dict[str, Any]) -> Dict[str, Any]:
    read_client = get_tool_client(tool_mode, incident_id, allow_write=False)
    pod_status = read_client.call_tool("get_pod_status", service=incident["service"], namespace=incident["namespace"])
    return {
        "pod_status": pod_status,
        "pod_describe": read_client.call_tool(
            "describe_pod",
            pod_name=pod_status["pod_name"],
            namespace=incident["namespace"],
        ),
        "metrics": read_client.call_tool("get_metrics", service=incident["service"], namespace=incident["namespace"]),
        "logs_tail": read_client.call_tool(
            "get_pod_logs",
            pod_name=pod_status["pod_name"],
            namespace=incident["namespace"],
            lines=200,
        )[-10:],
        "cluster_events": read_client.call_tool("get_cluster_events", namespace=incident["namespace"]),
    }


def execute_action(
    *,
    run_id: str,
    execution_mode: str,
    tool_mode: str,
    incident_id: str,
    incident: Dict[str, Any],
    action: Dict[str, Any],
    evidence_before: Dict[str, Any],
    diagnosis: str = "Unknown",
) -> Dict[str, Any]:
    normalized_action = normalize_action(action, incident)
    planned_command = render_action_to_command(normalized_action, incident)
    if execution_mode == "live" and is_alert_lab_incident(incident_id) and get_settings().demo_service_api_url:
        return execute_alert_lab_action(
            run_id=run_id,
            tool_mode=tool_mode,
            incident_id=incident_id,
            incident=incident,
            action=normalized_action,
            evidence_before=evidence_before,
            planned_command=planned_command,
            diagnosis=diagnosis,
        )

    if normalized_action["action_type"].startswith("gitops_"):
        result = GitOpsExecutor().apply_action(run_id=run_id or incident_id, action=normalized_action, incident=incident)
        return {
            "status": "completed",
            "execution_results": [result],
            "evidence_after": {},
            "improved": False,
            "improvement_summary": "GitOps change artifact was created. Merge and rollout happen outside the agent.",
            "command": planned_command,
            "verification": {},
        }

    if execution_mode == "live":
        client = KubectlMCPClient()
        result = client.execute_remediation(action=normalized_action, incident=incident)
        after = collect_evidence(tool_mode, incident_id, incident)
        verification = assess_verification_rules(diagnosis, evidence_before, after)
        improvement = assess_improvement(evidence_before, after)
        return {
            "status": "completed",
            "execution_results": [result],
            "evidence_after": after,
            "improved": bool(improvement["improved"] or verification.get("resolved", False)),
            "improvement_summary": improvement["summary"],
            "command": planned_command,
            "verification": verification,
        }

    client = get_tool_client(tool_mode, incident_id, allow_write=True)
    result = client.execute_remediation(action=normalized_action)
    after = collect_evidence(tool_mode, incident_id, incident)
    improvement = assess_improvement(evidence_before, after)
    verification = assess_verification_rules(diagnosis, evidence_before, after)
    return {
        "status": "completed",
        "execution_results": [result],
        "evidence_after": after,
        "improved": bool(improvement["improved"] or verification.get("resolved", False)),
        "improvement_summary": improvement["summary"],
        "command": planned_command,
        "verification": verification,
    }


def execute_rollback(
    *,
    run_id: str,
    execution_mode: str,
    tool_mode: str,
    incident_id: str,
    incident: Dict[str, Any],
    action: Dict[str, Any],
) -> Dict[str, Any]:
    rollback_action = rollback_action_for_action(action, incident)
    if rollback_action is None:
        return {
            "status": "not_available",
            "execution_results": [],
            "evidence_after": {},
            "improved": False,
            "improvement_summary": "No executable rollback action is available for this remediation.",
            "command": "",
            "verification": {},
        }

    rollback_command = rollback_command_for_action(action, incident)
    if rollback_action["action_type"].startswith("gitops_"):
        result = GitOpsExecutor().apply_rollback(run_id=run_id or incident_id, rollback_action=rollback_action, incident=incident)
        return {
            "status": "completed",
            "execution_results": [result],
            "evidence_after": {},
            "improved": False,
            "improvement_summary": "GitOps rollback artifact was created.",
            "command": rollback_command,
            "verification": {},
        }

    if execution_mode == "live":
        result = KubectlMCPClient().execute_remediation(action=rollback_action, incident=incident)
        return {
            "status": "completed",
            "execution_results": [result],
            "evidence_after": {},
            "improved": False,
            "improvement_summary": "Rollback executed against the live cluster.",
            "command": rollback_command,
            "verification": {},
        }

    client = get_tool_client(tool_mode, incident_id, allow_write=True)
    result = client.execute_remediation(action=rollback_action)
    after = collect_evidence(tool_mode, incident_id, incident)
    return {
        "status": "completed",
        "execution_results": [result],
        "evidence_after": after,
        "improved": False,
        "improvement_summary": "Rollback executed against the simulated environment.",
        "command": rollback_command,
        "verification": {},
    }
