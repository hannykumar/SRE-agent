"""
agent/langgraph_agent.py

Clean LangGraph workflow for the MCP-native SRE agent.

Graph:
Ingest -> Agent -> Tools -> Agent -> ...
                    |-> HITL_Interrupt -> END
                    |-> Execute -> END
                    |-> END
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from langgraph.graph import END, StateGraph

from agent.deterministic_policy import (
    evidence_from_tools,
    summarize_observation,
    tool_args_for,
)
from agent.evidence_graph import build_evidence_graph, rank_diagnoses
from agent.incident_catalog import catalog_version
from agent.model_runtime import passive_model_status
from agent.planner import normalize_action_from_decision, plan_next_step
from agent.prompts import SYSTEM_PROMPT, UNKNOWN_ESCALATION_OUTPUT
from agent.retrieval import build_retry_query, retrieve_runbook_chunks, summarize_retrieval_context
from agent.service_memory import enabled_integrations_summary, load_service_memory
from agent.specialists import build_specialist_findings, coordinate_specialist_findings
from agent.state import AgentState
from executor.client import ExecutorClient
from mcp_tools.actions import render_action_to_command, rollback_command_for_action
from mcp_tools.mock_mcp import MockMCP
from mcp_tools.tool_gateway import get_tool_client
from ops.artifacts import save_run_artifacts
from ops.metrics import PLANNER_DURATION, RETRIEVAL_DURATION, TOOL_DURATION, observe_duration
from ops.settings import get_settings
from tracing.trace_recorder import create_trace


os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
MAX_STEPS = 4
MIN_CONFIDENCE_FOR_ACTION = 0.55


def append_message(state: AgentState, role: str, content: str) -> None:
    state.setdefault("messages", []).append({"role": role, "content": content})


def trace_add(state: AgentState, event_type: str, **payload: Any) -> None:
    state.setdefault("trace", []).append({"type": event_type, **payload})


def normalize_execution_mode(value: Any) -> str:
    mode = str(value or "simulate").strip().lower()
    aliases = {
        "dry_run": "preview",
        "preview": "preview",
        "mock": "simulate",
        "simulate": "simulate",
        "real": "live",
        "live": "live",
    }
    return aliases.get(mode, "simulate")


def normalize_tool_mode(value: Any) -> str:
    mode = str(value or "direct").strip().lower()
    aliases = {
        "local": "direct",
        "direct": "direct",
        "mcp_mock": "mcp",
        "mcp": "mcp",
    }
    return aliases.get(mode, "direct")


def load_incident(incident_id: str) -> Dict[str, Any]:
    return MockMCP(incident_id).data


def _retrieve_chunks(
    query: str,
    *,
    limit: int,
    incident: Dict[str, Any] | None,
    evidence: Dict[str, Any] | None,
) -> List[Dict[str, Any]]:
    """
    Compatibility wrapper: older tests monkeypatch this module-level function with a
    simpler `(query, limit)` signature, while the production retrieval path accepts
    richer context.
    """
    try:
        return retrieve_runbook_chunks(query, limit=limit, incident=incident, evidence=evidence)
    except TypeError:
        return retrieve_runbook_chunks(query, limit=limit)


def _evidence_domain_count(evidence: Dict[str, Any]) -> int:
    domains = 0
    if dict(evidence.get("metrics") or {}):
        domains += 1
    if list(evidence.get("logs_tail") or []):
        domains += 1
    if list(evidence.get("cluster_events") or []):
        domains += 1
    if dict(evidence.get("prometheus") or {}):
        domains += 1
    if dict(evidence.get("loki") or {}):
        domains += 1
    if dict(evidence.get("tempo") or {}):
        domains += 1
    if list(evidence.get("recent_deploys") or []):
        domains += 1
    return domains


def build_final_response(state: AgentState) -> str:
    diagnosis = state.get("diagnosis", "Unknown")
    if diagnosis == "Unknown" or not state.get("confirmed", False):
        return UNKNOWN_ESCALATION_OUTPUT

    confidence = float(state.get("confidence", 0.0) or 0.0)
    confidence_label = "High" if confidence >= 0.85 else "Medium" if confidence >= 0.6 else "Low"
    command = state.get("planned_commands", ["(none)"])[0]
    coordinator = dict(state.get("coordinator_summary") or {})
    specialist_findings = list(state.get("specialist_findings") or [])
    top_candidate = (state.get("candidate_diagnoses") or [{}])[0]
    rationale = list(top_candidate.get("rationale") or [])[:3]
    citations = list(top_candidate.get("citations") or [])[:4]
    if not citations:
        citations = [str(node.get("evidence_id", "")) for node in list(state.get("evidence_graph") or [])[:4] if node.get("evidence_id")]

    lines = [
        f"Diagnosis: {diagnosis}. Confidence: {confidence_label}.",
        f"Coordinator summary: {coordinator.get('summary', 'The investigation gathered enough evidence to support this diagnosis.')}",
    ]
    if rationale:
        lines.append("Why this diagnosis: " + " | ".join(rationale))
    if specialist_findings:
        lines.append("Specialists: " + " | ".join(item.get("summary", "") for item in specialist_findings[:3] if item.get("summary")))
    lines.append(f"Proposed action: {command}")
    lines.append("Execution still requires explicit human approval.")
    if citations:
        lines.append("Citations: " + ", ".join(citations))
    return "\n".join(lines)


def _build_investigation_activity(
    *,
    retrieval_summary: str,
    coordinator_summary: Dict[str, Any],
    tool_name: str = "",
    tool_observation: str = "",
    next_step: str = "",
) -> List[Dict[str, Any]]:
    activity = [
        {"stage": "intake", "status": "completed", "summary": "Loaded incident context and service memory."},
        {"stage": "retrieval", "status": "completed", "summary": retrieval_summary},
    ]
    activity.extend(list(coordinator_summary.get("activity") or []))
    if tool_name:
        activity.append({"stage": "tool", "status": "completed", "summary": f"Collected {tool_name}: {tool_observation}"})
    if next_step:
        activity.append({"stage": "next_step", "status": "ready", "summary": next_step})
    return activity


def _tool_confirmation_metadata(tool_name: str, integrations: List[Dict[str, Any]]) -> Dict[str, Any]:
    for integration in integrations:
        tools = list(integration.get("tools") or [])
        if tool_name not in tools:
            continue
        confirmation_mode = str(integration.get("confirmation_mode", "auto"))
        safety_level = str(integration.get("safety_level", "low"))
        if confirmation_mode == "manual":
            return {
                "tool_confirmation_required": True,
                "tool_confirmation_tool": tool_name,
                "tool_confirmation_prompt": f"Confirm tool call for {tool_name} against {integration.get('name', 'external integration')} ({safety_level}).",
            }
    return {
        "tool_confirmation_required": False,
        "tool_confirmation_tool": "",
        "tool_confirmation_prompt": "",
    }


def node_ingest(state: AgentState) -> Dict[str, Any]:
    incident = state.get("incident") or load_incident(state["incident_id"])
    service_memory = load_service_memory(str(incident.get("service", "")), namespace=str(incident.get("namespace", "prod")))
    integration_summary = enabled_integrations_summary()
    retrieval_limit = max(get_settings().retrieval_limit, 3)
    retrieval_started = time.perf_counter()
    with observe_duration(RETRIEVAL_DURATION):
        retrieved = _retrieve_chunks(str(incident["description"]), limit=retrieval_limit, incident=incident, evidence={})
        retrieval = summarize_retrieval_context(str(incident["description"]), retrieved)
    retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000.0, 2)
    retrieved = retrieval["retrieved"]
    predicted_type = retrieval["predicted_type"]

    append_message(state, "system", SYSTEM_PROMPT)
    append_message(state, "user", incident["description"])

    trace_add(state, "ingest", incident_id=incident["incident_id"], predicted_type=predicted_type)
    trace_add(
        state,
        "retrieval",
        query=retrieval["query"],
        top_chunks=retrieved[:5],
        retrieval_quality=retrieval["retrieval_quality"],
        retrieval_explanation=retrieval.get("retrieval_explanation", {}),
        candidate_diagnoses=retrieval["candidate_diagnoses"][:3],
    )

    initial_state = {
        "incident": incident,
        "service_memory": service_memory,
        "integration_summary": integration_summary,
        "retrieved": retrieved,
        "top_chunks": retrieved[:5],
        "retrieval_query": retrieval["query"],
        "retrieval_quality": retrieval["retrieval_quality"],
        "retrieval_explanation": retrieval.get("retrieval_explanation", {}),
        "retrieval_attempts": 0,
        "candidate_diagnoses": retrieval["candidate_diagnoses"],
        "predicted_type": predicted_type,
    }
    evidence_graph = build_evidence_graph(initial_state)
    initial_findings = build_specialist_findings({}, (service_memory or {}).get("payload", {}))
    coordinator_summary = coordinate_specialist_findings(initial_findings, retrieval["candidate_diagnoses"])
    investigation_activity = _build_investigation_activity(
        retrieval_summary=f"Retrieved {len(retrieved[:5])} grounded runbook chunks for the initial hypothesis.",
        coordinator_summary=coordinator_summary,
        next_step=f"Coordinator focus: {coordinator_summary.get('focus', 'metrics')}",
    )

    return {
        "incident": incident,
        "infrastructure_memory": service_memory,
        "retrieved": retrieved,
        "top_chunks": retrieved[:5],
        "retrieval_query": retrieval["query"],
        "retrieval_quality": retrieval["retrieval_quality"],
        "retrieval_attempts": 0,
        "candidate_diagnoses": retrieval["candidate_diagnoses"],
        "predicted_type": predicted_type,
        "thought_summary": "",
        "escalation_reason": "",
        "planner_backend": "",
        "planner_error": "",
        "tool_results": {},
        "tool_mode": normalize_tool_mode(state.get("tool_mode", "mcp")),
        "messages": state.get("messages", []),
        "trace": state.get("trace", []),
        "step_count": 0,
        "max_steps": MAX_STEPS,
        "diagnosis": "Unknown",
        "confidence": 0.0,
        "confirmed": False,
        "requires_human_approval": False,
        "policy_ok": False,
        "policy_violations": [],
        "sanitized_plan": [],
        "plan": [],
        "execution_results": [],
        "planned_commands": [],
        "rollback_commands": [],
        "evidence": {},
        "evidence_graph": evidence_graph,
        "specialist_findings": initial_findings,
        "coordinator_summary": coordinator_summary,
        "investigation_activity": investigation_activity,
        "tool_confirmation_required": False,
        "tool_confirmation_prompt": "",
        "tool_confirmation_tool": "",
        "model_status": passive_model_status(),
        "runtime_health": {},
        "evidence_after": {},
        "improved": False,
        "improvement_summary": "",
        "latency_metrics": {"retrieval_ms": retrieval_ms},
    }


def node_agent(state: AgentState) -> Dict[str, Any]:
    step_count = int(state.get("step_count", 0)) + 1

    evidence = evidence_from_tools(state)
    planning_state = {**state, "step_count": step_count, "evidence": evidence}
    specialist_findings = build_specialist_findings(evidence, (state.get("service_memory") or {}).get("payload", {}))
    coordinator_summary = coordinate_specialist_findings(specialist_findings, state.get("candidate_diagnoses", []))
    planning_state.update({"specialist_findings": specialist_findings, "coordinator_summary": coordinator_summary})
    retrieval_attempts = int(state.get("retrieval_attempts", 0))
    retry_limit = max(get_settings().retrieval_retry_limit, 0)
    retrieval_limit = max(get_settings().retrieval_limit, 3)
    if (
        step_count > 1
        and retrieval_attempts < retry_limit
        and (
            state.get("predicted_type", "Unknown") == "Unknown"
            or bool((state.get("retrieval_quality") or {}).get("needs_retry"))
            or float(state.get("confidence", 0.0)) < 0.35
        )
    ):
        retry_query = build_retry_query(state["incident"], evidence, state.get("candidate_diagnoses", []))
        retry_started = time.perf_counter()
        with observe_duration(RETRIEVAL_DURATION):
            retry_chunks = _retrieve_chunks(retry_query, limit=retrieval_limit, incident=state["incident"], evidence=evidence)
            retry_retrieval = summarize_retrieval_context(retry_query, retry_chunks)
        trace_add(
            state,
            "retrieval_retry",
            query=retry_query,
            top_chunks=retry_retrieval["top_chunks"],
            retrieval_quality=retry_retrieval["retrieval_quality"],
            retrieval_explanation=retry_retrieval.get("retrieval_explanation", {}),
            candidate_diagnoses=retry_retrieval["candidate_diagnoses"][:3],
        )
        updated_latency = dict(state.get("latency_metrics", {}))
        updated_latency["retrieval_retry_ms"] = round((time.perf_counter() - retry_started) * 1000.0, 2)
        planning_state.update(
            {
                "retrieved": retry_retrieval["retrieved"],
                "top_chunks": retry_retrieval["top_chunks"],
                "retrieval_query": retry_retrieval["query"],
                "retrieval_quality": retry_retrieval["retrieval_quality"],
                "retrieval_explanation": retry_retrieval.get("retrieval_explanation", {}),
                "retrieval_attempts": retrieval_attempts + 1,
                "candidate_diagnoses": retry_retrieval["candidate_diagnoses"],
                "predicted_type": retry_retrieval["predicted_type"],
                "latency_metrics": updated_latency,
            }
        )
        coordinator_summary = coordinate_specialist_findings(specialist_findings, retry_retrieval["candidate_diagnoses"])
        planning_state["coordinator_summary"] = coordinator_summary

    planner_started = time.perf_counter()
    with observe_duration(PLANNER_DURATION):
        decision, planner_backend, planner_error = plan_next_step(planning_state)
    analysis_state = {**planning_state, "specialist_findings": specialist_findings}
    diagnosis_candidates = rank_diagnoses(analysis_state)
    evidence_graph = build_evidence_graph(analysis_state)
    thought = decision.thought_summary.strip()
    confidence = float(decision.confidence)
    diagnosis_name = decision.hypothesis.strip() or "Unknown"
    next_predicted_type = diagnosis_name if diagnosis_name != "Unknown" else planning_state.get("predicted_type", "Unknown")
    updated_latency = dict(planning_state.get("latency_metrics", {}))
    updated_latency["planner_ms"] = round((time.perf_counter() - planner_started) * 1000.0, 2)

    append_message(state, "assistant", thought)

    retrieval_quality = dict(planning_state.get("retrieval_quality") or {})
    strong_evidence = _evidence_domain_count(evidence) >= 2
    if (
        decision.decision == "propose_action"
        and diagnosis_name != "Unknown"
        and confidence >= MIN_CONFIDENCE_FOR_ACTION
        and (strong_evidence or float(retrieval_quality.get("overall", 0.0)) >= 0.72)
    ):
        proposed_action = normalize_action_from_decision(planning_state, decision)
        planned_command = render_action_to_command(proposed_action, state["incident"])
        rollback = rollback_command_for_action(proposed_action, state["incident"])

        trace_add(
            state,
            "agent_decision",
            step_id=step_count,
            hypothesis=diagnosis_name,
            thought=thought,
            confidence_before=state.get("confidence", 0.0),
            confidence_after=confidence,
            decision="propose_remediation",
            planner_backend=planner_backend,
            planner_error=planner_error,
        )
        investigation_activity = _build_investigation_activity(
            retrieval_summary=f"Retrieved {len(planning_state.get('top_chunks', [])[:5])} grounded runbook chunks.",
            coordinator_summary=coordinator_summary,
            next_step="Prepared a remediation recommendation and paused for human approval.",
        )

        return {
            "step_count": step_count,
            "predicted_type": next_predicted_type,
            "diagnosis": diagnosis_name,
            "confidence": confidence,
            "confirmed": True,
            "evidence": evidence,
            "evidence_graph": evidence_graph,
            "specialist_findings": specialist_findings,
            "coordinator_summary": coordinator_summary,
            "investigation_activity": investigation_activity,
            "proposed_action": proposed_action,
            "requires_human_approval": True,
            "plan": [proposed_action],
            "sanitized_plan": [proposed_action],
            "policy_ok": True,
            "planned_commands": [planned_command],
            "rollback_commands": [rollback],
            "thought_summary": thought,
            "escalation_reason": "",
            "planner_backend": planner_backend,
            "planner_error": planner_error,
            "retrieved": planning_state.get("retrieved", []),
            "top_chunks": planning_state.get("top_chunks", []),
            "retrieval_query": planning_state.get("retrieval_query", ""),
            "retrieval_quality": planning_state.get("retrieval_quality", {}),
            "retrieval_explanation": planning_state.get("retrieval_explanation", {}),
            "retrieval_attempts": planning_state.get("retrieval_attempts", retrieval_attempts),
            "candidate_diagnoses": diagnosis_candidates,
            "latency_metrics": updated_latency,
            "tool_confirmation_required": False,
            "tool_confirmation_prompt": "",
            "tool_confirmation_tool": "",
            "model_status": passive_model_status(),
        }

    if decision.decision == "propose_action" and step_count <= int(state.get("max_steps", MAX_STEPS)):
        next_tool = str(coordinator_summary.get("recommended_next_tool") or "").strip() or "get_cluster_events"
        tool_args = tool_args_for(next_tool, planning_state)
        tool_confirmation = _tool_confirmation_metadata(next_tool, state.get("integration_summary", []))
        investigation_activity = _build_investigation_activity(
            retrieval_summary=f"Retrieved {len(planning_state.get('top_chunks', [])[:5])} grounded runbook chunks.",
            coordinator_summary=coordinator_summary,
            next_step=f"The model tried to propose remediation early, so the workflow is collecting more evidence with {next_tool}.",
        )
        trace_add(
            state,
            "agent_decision",
            step_id=step_count,
            hypothesis=next_predicted_type,
            thought=thought,
            action="call_tool",
            tool_name=next_tool,
            tool_args=tool_args,
            confidence_before=state.get("confidence", 0.0),
            confidence_after=confidence,
            decision="guardrail_more_evidence",
            planner_backend=planner_backend,
            planner_error=planner_error,
        )
        return {
            "step_count": step_count,
            "predicted_type": next_predicted_type,
            "next_tool": next_tool,
            "next_tool_args": tool_args,
            "diagnosis": "Unknown",
            "confidence": confidence,
            "confirmed": False,
            "evidence": evidence,
            "specialist_findings": specialist_findings,
            "coordinator_summary": coordinator_summary,
            "investigation_activity": investigation_activity,
            "thought_summary": thought,
            "planner_backend": planner_backend,
            "planner_error": planner_error,
            "retrieved": planning_state.get("retrieved", []),
            "top_chunks": planning_state.get("top_chunks", []),
            "retrieval_query": planning_state.get("retrieval_query", ""),
            "retrieval_quality": retrieval_quality,
            "retrieval_explanation": planning_state.get("retrieval_explanation", {}),
            "retrieval_attempts": planning_state.get("retrieval_attempts", retrieval_attempts),
            "candidate_diagnoses": diagnosis_candidates,
            "evidence_graph": evidence_graph,
            "latency_metrics": updated_latency,
            "model_status": passive_model_status(),
            **tool_confirmation,
        }

    if decision.decision == "call_tool" and decision.tool_name and step_count <= int(state.get("max_steps", MAX_STEPS)):
        next_tool = decision.tool_name
        tool_args = tool_args_for(next_tool, planning_state)
        tool_confirmation = _tool_confirmation_metadata(next_tool, state.get("integration_summary", []))
        investigation_activity = _build_investigation_activity(
            retrieval_summary=f"Retrieved {len(planning_state.get('top_chunks', [])[:5])} grounded runbook chunks.",
            coordinator_summary=coordinator_summary,
            next_step=f"Next the assistant will collect {next_tool}.",
        )

        trace_add(
            state,
            "agent_decision",
            step_id=step_count,
            hypothesis=next_predicted_type,
            thought=thought,
            action="call_tool",
            tool_name=next_tool,
            tool_args=tool_args,
            confidence_before=state.get("confidence", 0.0),
            confidence_after=confidence,
            decision="tool_call",
            planner_backend=planner_backend,
            planner_error=planner_error,
        )

        return {
            "step_count": step_count,
            "predicted_type": next_predicted_type,
            "next_tool": next_tool,
            "next_tool_args": tool_args,
            "diagnosis": "Unknown",
            "confidence": confidence,
            "confirmed": False,
            "evidence": evidence,
            "specialist_findings": specialist_findings,
            "coordinator_summary": coordinator_summary,
            "investigation_activity": investigation_activity,
            "thought_summary": thought,
            "planner_backend": planner_backend,
            "planner_error": planner_error,
            "retrieved": planning_state.get("retrieved", []),
            "top_chunks": planning_state.get("top_chunks", []),
            "retrieval_query": planning_state.get("retrieval_query", ""),
            "retrieval_quality": planning_state.get("retrieval_quality", {}),
            "retrieval_explanation": planning_state.get("retrieval_explanation", {}),
            "retrieval_attempts": planning_state.get("retrieval_attempts", retrieval_attempts),
            "candidate_diagnoses": diagnosis_candidates,
            "evidence_graph": evidence_graph,
            "latency_metrics": updated_latency,
            "model_status": passive_model_status(),
            **tool_confirmation,
        }

    escalation_reason = decision.escalation_reason or "Tool observations did not produce clear evidence."
    append_message(state, "assistant", UNKNOWN_ESCALATION_OUTPUT)
    trace_add(
        state,
        "agent_decision",
        step_id=step_count,
        hypothesis="Unknown",
        thought=thought,
        confidence_before=state.get("confidence", 0.0),
        confidence_after=min(confidence or 0.2, 0.2),
        decision="escalate",
        escalation_reason=escalation_reason,
        planner_backend=planner_backend,
        planner_error=planner_error,
    )
    investigation_activity = _build_investigation_activity(
        retrieval_summary=f"Retrieved {len(planning_state.get('top_chunks', [])[:5])} grounded runbook chunks.",
        coordinator_summary=coordinator_summary,
        next_step="Evidence stayed weak, so the run escalated instead of guessing.",
    )

    return {
        "step_count": step_count,
        "diagnosis": "Unknown",
        "confidence": min(confidence or 0.2, 0.2),
        "confirmed": False,
        "evidence": evidence,
        "evidence_graph": evidence_graph,
        "specialist_findings": specialist_findings,
        "coordinator_summary": coordinator_summary,
        "investigation_activity": investigation_activity,
        "final_response": UNKNOWN_ESCALATION_OUTPUT,
        "planned_commands": [],
        "rollback_commands": [],
        "requires_human_approval": False,
        "policy_ok": False,
        "plan": [],
        "sanitized_plan": [],
        "thought_summary": thought,
        "escalation_reason": escalation_reason,
        "planner_backend": planner_backend,
        "planner_error": planner_error,
        "retrieved": planning_state.get("retrieved", []),
        "top_chunks": planning_state.get("top_chunks", []),
        "retrieval_query": planning_state.get("retrieval_query", ""),
        "retrieval_quality": planning_state.get("retrieval_quality", {}),
        "retrieval_explanation": planning_state.get("retrieval_explanation", {}),
        "retrieval_attempts": planning_state.get("retrieval_attempts", retrieval_attempts),
        "candidate_diagnoses": diagnosis_candidates,
        "latency_metrics": updated_latency,
        "tool_confirmation_required": False,
        "tool_confirmation_prompt": "",
        "tool_confirmation_tool": "",
        "model_status": passive_model_status(),
    }


def node_tools(state: AgentState) -> Dict[str, Any]:
    tool_name = state["next_tool"]
    tool_args = state.get("next_tool_args", {})
    client = get_tool_client(state.get("tool_mode", "mcp"), state["incident_id"], allow_write=False)
    tool_started = time.perf_counter()
    with observe_duration(TOOL_DURATION, tool_name=tool_name):
        observation = client.call_tool(tool_name, **tool_args)

    updated_results = dict(state.get("tool_results", {}))
    updated_results[tool_name] = observation
    observation_summary = summarize_observation(tool_name, observation)

    append_message(state, "tool", f"{tool_name}: {observation_summary}")
    trace_add(
        state,
        "tool_observation",
        tool_name=tool_name,
        tool_args=tool_args,
        observation=observation_summary,
    )

    updated_evidence = evidence_from_tools({**state, "tool_results": updated_results})
    specialist_findings = build_specialist_findings(updated_evidence, (state.get("service_memory") or {}).get("payload", {}))
    coordinator_summary = coordinate_specialist_findings(specialist_findings, state.get("candidate_diagnoses", []))
    return {
        "tool_results": updated_results,
        "evidence": updated_evidence,
        "evidence_graph": build_evidence_graph({**state, "tool_results": updated_results, "evidence": updated_evidence}),
        "specialist_findings": specialist_findings,
        "coordinator_summary": coordinator_summary,
        "investigation_activity": _build_investigation_activity(
            retrieval_summary=f"Retrieved {len(state.get('top_chunks', [])[:5])} grounded runbook chunks.",
            coordinator_summary=coordinator_summary,
            tool_name=tool_name,
            tool_observation=observation_summary,
            next_step="The planner will review the new observation and decide whether to continue or recommend an action.",
        ),
        "next_tool": "",
        "next_tool_args": {},
        "tool_confirmation_required": False,
        "tool_confirmation_prompt": "",
        "tool_confirmation_tool": "",
        "model_status": passive_model_status(),
        "latency_metrics": {
            **dict(state.get("latency_metrics", {})),
            f"tool_{tool_name}_ms": round((time.perf_counter() - tool_started) * 1000.0, 2),
        },
    }


def node_hitl_interrupt(state: AgentState) -> Dict[str, Any]:
    command = state.get("planned_commands", ["(none)"])[0]
    prompt = f"Proposed command: {command}. Approve? Y/N"
    trace_add(state, "hitl_interrupt", prompt=prompt, action=state.get("proposed_action", {}))
    return {"approval_prompt": prompt}


def node_execute(state: AgentState) -> Dict[str, Any]:
    action = state.get("proposed_action", {})
    incident = state["incident"]
    mode = normalize_execution_mode(state.get("execution_mode", "preview"))
    planned_command = render_action_to_command(action, incident)

    if mode == "preview":
        result = {"status": "preview", "tool": "execute_remediation", "command": planned_command, "action": action}
        trace_add(state, "execute", mode=mode, result=result)
        return {
            "execution_results": [result],
            "rollback_record": {
                "status": "ready" if state.get("rollback_commands") else "not_available",
                "rollback_command": state.get("rollback_commands", [""])[0] if state.get("rollback_commands") else "",
            },
            "executor_transport": "none",
            "evidence_after": {},
            "verification": {},
            "improved": False,
            "improvement_summary": "Preview only. No changes were applied.",
        }

    execution_id = f"{state.get('run_id') or 'local'}_{uuid4().hex[:8]}"
    response = ExecutorClient().execute(
        {
            "execution_id": execution_id,
            "run_id": state.get("run_id", ""),
            "incident_id": state["incident_id"],
            "incident": incident,
            "action": action,
            "diagnosis": state.get("diagnosis", "Unknown"),
            "execution_mode": mode,
            "tool_mode": state.get("tool_mode", "mcp"),
            "evidence_before": state.get("evidence", {}),
            "rollback_commands": state.get("rollback_commands", []),
        }
    )
    trace_add(
        state,
        "execute",
        mode=mode,
        result=response.get("execution_results", [{}])[0] if response.get("execution_results") else {},
        improvement={
            "improved": response.get("improved", False),
            "summary": response.get("improvement_summary", ""),
        },
        executor_transport=response.get("executor_transport", "embedded"),
    )

    return {
        "execution_results": response.get("execution_results", []),
        "rollback_record": response.get("rollback_record", {}),
        "executor_transport": response.get("executor_transport", "embedded"),
        "evidence_after": response.get("evidence_after", {}),
        "verification": response.get("verification", {}),
        "improved": bool(response.get("improved", False)),
        "improvement_summary": response.get("improvement_summary", ""),
    }


def route_after_agent(state: AgentState) -> str:
    if state.get("proposed_action"):
        return "execute" if bool(state.get("approved", False)) else "interrupt"
    if state.get("next_tool"):
        return "tools"
    if state.get("final_response"):
        return "end"
    return "end"


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("ingest", node_ingest)
    graph.add_node("agent", node_agent)
    graph.add_node("tools", node_tools)
    graph.add_node("hitl_interrupt", node_hitl_interrupt)
    graph.add_node("execute", node_execute)

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "agent")
    graph.add_conditional_edges(
        "agent",
        route_after_agent,
        {
            "tools": "tools",
            "interrupt": "hitl_interrupt",
            "execute": "execute",
            "end": END,
        },
    )
    graph.add_edge("tools", "agent")
    graph.add_edge("hitl_interrupt", END)
    graph.add_edge("execute", END)
    return graph.compile()


GRAPH = build_graph()


def persist_trace(state: AgentState) -> str:
    trace = create_trace(state["incident_id"])
    for event in state.get("trace", []):
        event_type = event.get("type", "trace")
        trace.add(event_type, **{k: v for k, v in event.items() if k != "type"})
    return str(trace.save())


def run_incident_langgraph(
    incident_id: str,
    approved: bool = False,
    execution_mode: str = "preview",
    tool_mode: str = "mcp",
    run_id: str = "",
    incident_payload: Dict[str, Any] | None = None,
    save_artifacts_enabled: bool = True,
) -> Dict[str, Any]:
    if incident_payload is not None:
        MockMCP.seed_live_state(incident_id, incident_payload)

    final_state = GRAPH.invoke(
        {
            "run_id": run_id,
            "incident_id": incident_id,
            "incident": incident_payload or {},
            "approved": approved,
            "execution_mode": normalize_execution_mode(execution_mode),
            "tool_mode": normalize_tool_mode(tool_mode),
        }
    )

    final_state["final_response"] = build_final_response(final_state)
    final_state["saved_trace"] = persist_trace(final_state)
    final_state["rca_draft"] = final_state["final_response"]
    final_state["rca_final"] = final_state["final_response"]

    result = {
        "run_id": run_id,
        "incident_id": incident_id,
        "system_prompt": SYSTEM_PROMPT,
        "execution_mode": normalize_execution_mode(execution_mode),
        "tool_mode": normalize_tool_mode(tool_mode),
        "predicted_type": final_state.get("predicted_type", "Unknown"),
        "planner_backend": final_state.get("planner_backend", ""),
        "planner_error": final_state.get("planner_error", ""),
        "diagnosis": final_state.get("diagnosis", "Unknown"),
        "confidence": float(final_state.get("confidence", 0.0)),
        "confirmed": bool(final_state.get("confirmed", False)),
        "top_chunks": final_state.get("top_chunks", []),
        "retrieved": final_state.get("retrieved", []),
        "retrieval_query": final_state.get("retrieval_query", ""),
        "retrieval_quality": final_state.get("retrieval_quality", {}),
        "retrieval_explanation": final_state.get("retrieval_explanation", {}),
        "candidate_diagnoses": final_state.get("candidate_diagnoses", []),
        "service_memory": final_state.get("service_memory", {}),
        "infrastructure_memory": final_state.get("infrastructure_memory", final_state.get("service_memory", {})),
        "integration_summary": final_state.get("integration_summary", []),
        "messages": final_state.get("messages", []),
        "trace": final_state.get("trace", []),
        "evidence": final_state.get("evidence", {}),
        "evidence_graph": final_state.get("evidence_graph", []),
        "specialist_findings": final_state.get("specialist_findings", []),
        "coordinator_summary": final_state.get("coordinator_summary", {}),
        "investigation_activity": final_state.get("investigation_activity", []),
        "tool_confirmation_required": bool(final_state.get("tool_confirmation_required", False)),
        "tool_confirmation_prompt": final_state.get("tool_confirmation_prompt", ""),
        "tool_confirmation_tool": final_state.get("tool_confirmation_tool", ""),
        "model_status": final_state.get("model_status", passive_model_status()),
        "runtime_health": final_state.get("runtime_health", {}),
        "thought_summary": final_state.get("thought_summary", ""),
        "escalation_reason": final_state.get("escalation_reason", ""),
        "plan": final_state.get("plan", []),
        "proposed_action": final_state.get("proposed_action", {}),
        "requires_human_approval": bool(final_state.get("requires_human_approval", False)),
        "approval_prompt": final_state.get("approval_prompt", ""),
        "policy_ok": bool(final_state.get("policy_ok", False)),
        "policy_violations": final_state.get("policy_violations", []),
        "sanitized_plan": final_state.get("sanitized_plan", []),
        "planned_commands": final_state.get("planned_commands", []),
        "rollback_commands": final_state.get("rollback_commands", []),
        "rollback_record": final_state.get("rollback_record", {}),
        "executor_transport": final_state.get("executor_transport", ""),
        "execution_results": final_state.get("execution_results", []),
        "evidence_after": final_state.get("evidence_after", {}),
        "verification": final_state.get("verification", {}),
        "improved": bool(final_state.get("improved", False)),
        "improvement_summary": final_state.get("improvement_summary", ""),
        "final_response": final_state.get("final_response", ""),
        "rca_draft": final_state.get("rca_draft", ""),
        "rca_final": final_state.get("rca_final", ""),
        "saved_trace": final_state.get("saved_trace", ""),
        "latency_metrics": final_state.get("latency_metrics", {}),
        "catalog_version": catalog_version(),
    }
    verification = dict(result.get("verification") or {})
    if verification:
        result["verification_outcome"] = str(
            verification.get("status") or ("resolved" if verification.get("resolved") else "unchanged")
        ).strip().lower()
    else:
        result["verification_outcome"] = "not_run"

    if save_artifacts_enabled:
        artifact_dir = save_run_artifacts(result)
        result["artifact_dir"] = str(artifact_dir)

    return result
