from __future__ import annotations

import re
from typing import Any, Dict, List

from agent.evidence_graph import confirmation_from_rankings, rank_diagnoses
from agent.incident_catalog import allowed_catalog_actions, allowed_catalog_tools, incident_definition
from agent.state import AgentState, StructuredAction


ALLOWED_READ_TOOLS = allowed_catalog_tools()
ALLOWED_ACTION_TYPES = allowed_catalog_actions()


def infer_incident_type_from_filename(name: str) -> str:
    lowered = name.lower()
    if "database" in lowered or "db_" in lowered or "db-" in lowered:
        return "DatabaseConnectionFailure"
    if "tls" in lowered or "certificate" in lowered or "x509" in lowered:
        return "TLSCertificateExpiry"
    if "node_not_ready" in lowered or "node" in lowered:
        return "NodeNotReady"
    if "disk" in lowered or "storage" in lowered:
        return "DiskPressure"
    if "queue" in lowered or "worker_lag" in lowered or "backlog" in lowered:
        return "QueueBacklog"
    if "dependency" in lowered or "latency" in lowered:
        return "DependencyLatency"
    if "deploy" in lowered or "regression" in lowered or "release" in lowered:
        return "DeploymentRegression"
    if "dns" in lowered:
        return "DNSFailure"
    if "503" in lowered or "upstream" in lowered:
        return "Service503"
    if "crashloop" in lowered or "oom" in lowered:
        return "CrashLoopBackOff"
    if "cpu" in lowered:
        return "HighCPU"
    if "memory" in lowered:
        return "HighMemory"
    return "Unknown"


def choose_primary_incident_type(retrieved: List[Dict[str, Any]]) -> str:
    if not retrieved:
        return "Unknown"
    first = str(retrieved[0].get("incident_type") or "Unknown")
    first_score = float(retrieved[0].get("score", 0.0) or 0.0)
    second_score = float(retrieved[1].get("score", 0.0) or 0.0) if len(retrieved) > 1 else 0.0
    if first == "Unknown" or second_score >= first_score * 0.95:
        return "Unknown"
    return first


def tool_args_for(tool_name: str, state: AgentState) -> Dict[str, Any]:
    incident = state["incident"]
    pod_status = state.get("tool_results", {}).get("get_pod_status", {})
    pod_name = pod_status.get("pod_name") or incident.get("pod_status", {}).get("pod_name")
    service = str(incident.get("service", ""))
    namespace = str(incident.get("namespace", "default"))

    if tool_name == "get_pod_status":
        return {"service": service, "namespace": namespace}
    if tool_name == "describe_pod":
        return {"pod_name": pod_name, "namespace": namespace}
    if tool_name == "get_metrics":
        return {"service": service, "namespace": namespace}
    if tool_name == "get_pod_logs":
        return {"pod_name": pod_name, "namespace": namespace, "lines": 200}
    if tool_name == "get_cluster_events":
        return {"namespace": namespace}
    if tool_name == "query_prometheus":
        return {"expression": f"service_health_score{{service=\"{service}\",namespace=\"{namespace}\"}}"}
    if tool_name == "query_loki":
        return {"service": service, "namespace": namespace, "limit": 50}
    if tool_name == "query_tempo":
        return {"service": service, "namespace": namespace, "limit": 20}
    if tool_name == "get_dashboard_context":
        return {"service": service, "namespace": namespace}
    if tool_name == "get_recent_deploys":
        return {"service": service, "namespace": namespace}
    if tool_name == "get_service_owner":
        return {"service": service}
    if tool_name == "get_incident_history":
        return {"service": service, "limit": 10}
    return {"service": service, "namespace": namespace}


def summarize_observation(tool_name: str, observation: Any) -> str:
    if tool_name == "get_pod_status":
        return f"pod={observation.get('pod_name')} restarts={observation.get('restarts')} phase={observation.get('phase')}"
    if tool_name == "describe_pod":
        reason = observation.get("last_state", {}).get("terminated", {}).get("reason", "unknown")
        return f"last_termination_reason={reason}"
    if tool_name == "get_metrics":
        parts = []
        for key in ["cpu_percent", "memory_percent", "error_rate_percent", "dns_error_rate_percent", "p95_latency_ms"]:
            if key in observation:
                parts.append(f"{key}={observation[key]}")
        return ", ".join(parts) if parts else "metrics collected"
    if tool_name == "get_pod_logs":
        logs = observation[-2:] if isinstance(observation, list) else []
        return " | ".join(logs)
    if tool_name == "get_cluster_events":
        events = observation[-2:] if isinstance(observation, list) else []
        return " | ".join(events)
    if tool_name == "query_prometheus":
        return f"prometheus_query={observation.get('expression', '')} value={observation.get('value', 'n/a')}"
    if tool_name == "query_loki":
        logs = observation.get("lines", [])[-2:] if isinstance(observation, dict) else []
        return " | ".join(logs)
    if tool_name == "query_tempo":
        spans = observation.get("spans", []) if isinstance(observation, dict) else []
        return "; ".join(f"{span.get('span')}:{span.get('error', '')}" for span in spans[:2])
    if tool_name == "get_dashboard_context":
        return f"dashboard={observation.get('dashboard_uid', 'n/a')} panels={len(observation.get('panels', []))}"
    if tool_name == "get_recent_deploys":
        deploys = observation if isinstance(observation, list) else []
        if not deploys:
            return "no recent deploys"
        latest = deploys[0]
        return f"latest_deploy={latest.get('version', 'unknown')} {latest.get('minutes_ago', 'n/a')}m ago"
    if tool_name == "get_service_owner":
        return f"team={observation.get('team', 'unknown')} pagerduty={observation.get('pagerduty', 'n/a')}"
    if tool_name == "get_incident_history":
        incidents = observation if isinstance(observation, list) else []
        if not incidents:
            return "no recent incident history"
        latest = incidents[0]
        return f"history={latest.get('type', 'unknown')} {latest.get('hours_ago', 'n/a')}h ago"
    return str(observation)


def evidence_from_tools(state: AgentState) -> Dict[str, Any]:
    tools = state.get("tool_results", {})
    return {
        "pod_status": tools.get("get_pod_status", state.get("incident", {}).get("pod_status", {})),
        "pod_describe": tools.get("describe_pod", state.get("incident", {}).get("pod_describe", {})),
        "metrics": tools.get("get_metrics", state.get("incident", {}).get("metrics", {})),
        "logs_tail": tools.get("get_pod_logs", state.get("incident", {}).get("logs_tail", [])),
        "cluster_events": tools.get("get_cluster_events", state.get("incident", {}).get("cluster_events", [])),
        "prometheus": tools.get("query_prometheus", {}),
        "loki": tools.get("query_loki", {}),
        "tempo": tools.get("query_tempo", {}),
        "dashboard_context": tools.get("get_dashboard_context", {}),
        "recent_deploys": tools.get("get_recent_deploys", []),
        "service_owner": tools.get("get_service_owner", {}),
        "incident_history": tools.get("get_incident_history", []),
    }


def diagnose_from_evidence(state: AgentState) -> Dict[str, Any]:
    rankings = rank_diagnoses({**state, "evidence": evidence_from_tools(state)})
    diagnosis, confidence, confirmed = confirmation_from_rankings(rankings)
    return {
        "diagnosis": diagnosis,
        "confidence": confidence,
        "confirmed": confirmed,
        "rankings": rankings,
    }


def missing_tool(state: AgentState) -> str:
    seen = state.get("tool_results", {})
    predicted_type = state.get("predicted_type", "Unknown")
    definition = incident_definition(predicted_type) if predicted_type != "Unknown" else None
    if definition is None and state.get("candidate_diagnoses"):
        top_candidate = str(state.get("candidate_diagnoses", [{}])[0].get("incident_type", "Unknown"))
        definition = incident_definition(top_candidate)
    sequence = list(definition.discriminating_tools) if definition else [
        "get_pod_status",
        "get_metrics",
        "get_pod_logs",
        "describe_pod",
        "get_cluster_events",
        "get_recent_deploys",
        "get_service_owner",
        "get_incident_history",
    ]
    for tool_name in sequence:
        if tool_name not in seen:
            return tool_name
    return ""


def _condition_matches(incident: Dict[str, Any], evidence: Dict[str, Any], condition: Dict[str, Any]) -> bool:
    path = str(condition.get("path", ""))
    if path.startswith("incident."):
        current: Any = incident
        path = path[len("incident."):]
    else:
        current = {**evidence, "incident": incident}
    for part in path.split("."):
        if not part:
            continue
        if not isinstance(current, dict):
            return False
        current = current.get(part)
    if "equals" in condition:
        return current == condition["equals"]
    if "lte" in condition:
        try:
            return float(current) <= float(condition["lte"])
        except Exception:
            return False
    if "gte" in condition:
        try:
            return float(current) >= float(condition["gte"])
        except Exception:
            return False
    return False


def build_action(incident: Dict[str, Any], diagnosis: str, evidence: Dict[str, Any]) -> StructuredAction:
    definition = incident_definition(diagnosis)
    namespace = str(incident.get("namespace") or "default")
    service = str(incident.get("service") or "service")
    replicas = int((evidence.get("pod_status") or {}).get("replicas", incident.get("pod_status", {}).get("replicas", 1)) or 1)

    if definition is None:
        return {
            "action_type": "restart_pod",
            "target": service,
            "namespace": namespace,
            "reason": "Fallback restart because the diagnosis did not map to a catalog action.",
        }

    for template in definition.safe_actions:
        conditions = list(template.get("conditions", []))
        if conditions and not all(_condition_matches(incident, evidence, condition) for condition in conditions):
            continue
        params = dict(template.get("params", {}))
        action_type = str(template.get("action_type", "restart_pod"))
        action: StructuredAction = {
            "action_type": action_type,
            "target": str(template.get("target") or ("coredns" if action_type == "restart_coredns" else service)),
            "namespace": str(template.get("namespace") or ("kube-system" if action_type == "restart_coredns" else namespace)),
            "reason": str(template.get("reason", "Safe catalog action.")),
        }
        if action_type in {"scale_deployment", "gitops_scale_deployment"}:
            action["replicas"] = int(params.get("replicas", max(replicas + 1, 2)))
            action["previous_replicas"] = replicas
        if action_type == "gitops_scale_deployment":
            action["execution_model"] = "gitops"
            action["manifest_path"] = str(incident.get("gitops_manifest_path") or f"clusters/{namespace}/{service}.json")
        return action

    return {
        "action_type": "restart_pod",
        "target": service,
        "namespace": namespace,
        "reason": f"Fallback restart for {diagnosis} because no catalog template matched.",
    }


def deterministic_decision(state: AgentState) -> Dict[str, Any]:
    evidence = evidence_from_tools(state)
    diagnosis = diagnose_from_evidence({**state, "evidence": evidence})
    diagnosis_name = diagnosis["diagnosis"]
    confidence = float(diagnosis["confidence"])

    if bool(diagnosis["confirmed"]):
        proposed_action = build_action(state["incident"], diagnosis_name, evidence)
        top_ranking = (diagnosis.get("rankings") or [{}])[0]
        rationale = "; ".join(top_ranking.get("rationale", [])) or f"Evidence is strong enough to diagnose {diagnosis_name}."
        return {
            "thought_summary": rationale,
            "hypothesis": diagnosis_name,
            "confidence": confidence,
            "decision": "propose_action",
            "tool_name": None,
            "proposed_action": proposed_action,
            "escalation_reason": None,
        }

    next_tool = missing_tool(state)
    if next_tool and int(state.get("step_count", 0)) <= int(state.get("max_steps", 4)):
        return {
            "thought_summary": f"Current evidence is not enough. Call {next_tool} next to verify the hypothesis.",
            "hypothesis": state.get("predicted_type", "Unknown"),
            "confidence": confidence,
            "decision": "call_tool",
            "tool_name": next_tool,
            "proposed_action": None,
            "escalation_reason": None,
        }

    return {
        "thought_summary": "The evidence is still insufficient after the allowed tool budget. Escalate to a human SRE.",
        "hypothesis": "Unknown",
        "confidence": 0.2,
        "decision": "escalate",
        "tool_name": None,
        "proposed_action": None,
        "escalation_reason": "Tool observations did not produce clear evidence.",
    }


def lexical_terms(query: str) -> List[str]:
    return [token for token in re.split(r"\W+", query.lower()) if len(token) > 2]
