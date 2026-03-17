from __future__ import annotations

import json
from typing import Any, Dict, List

import agent.langgraph_agent as lg
from agent.model_runtime import reset_model_runtime_state
import agent.planner as planner
from mcp_tools.mock_mcp import MockMCP
from ops.settings import reset_settings_cache


def _fake_retrieval(_query: str, limit: int = 8) -> List[Dict[str, Any]]:
    return [
        {
            "score": 1.0,
            "source_file": "01_crashloopbackoff_oomkilled.md",
            "incident_type": "CrashLoopBackOff",
            "section": "Symptoms",
            "text": "OOMKilled and CrashLoopBackOff observed",
        }
    ][:limit]


def _fake_retrieval_for_query(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    lowered = query.lower()
    if "nxdomain" in lowered or "resolve" in lowered or "dns" in lowered:
        return [
            {
                "score": 1.0,
                "source_file": "05_dns_resolution_failures.md",
                "incident_type": "DNSFailure",
                "section": "Symptoms",
                "text": "Resolver failures and NXDOMAIN errors indicate DNS issues.",
            }
        ][:limit]
    if "503" in lowered or "upstream" in lowered:
        return [
            {
                "score": 1.0,
                "source_file": "04_service_503_upstream_down.md",
                "incident_type": "Service503",
                "section": "Symptoms",
                "text": "503 responses with upstream timeouts indicate dependency failure.",
            }
        ][:limit]
    return _fake_retrieval(query, limit)


class _FakeOllamaResponse:
    def __init__(self, payload: Dict[str, Any]):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def setup_function() -> None:
    reset_model_runtime_state()
    MockMCP.reset_live_state()


def test_ollama_planner_path(monkeypatch) -> None:
    monkeypatch.setenv("SRE_AGENT_PLANNER_PROVIDER", "ollama")
    monkeypatch.setenv("SRE_AGENT_PLANNER_MODEL", "qwen3:4b")
    reset_settings_cache()
    MockMCP.reset_live_state()
    monkeypatch.setattr(lg, "retrieve_runbook_chunks", _fake_retrieval)

    ollama_payload = {
        "message": {
            "content": json.dumps(
                {
                    "thought_summary": "OOM evidence is already clear from the incident context. Propose a safe GitOps scale-up.",
                    "hypothesis": "CrashLoopBackOff",
                    "confidence": 0.94,
                    "decision": "propose_action",
                    "tool_name": None,
                    "proposed_action": {
                        "action_type": "gitops_scale_deployment",
                        "target": "checkout",
                        "namespace": "prod",
                        "replicas": 2,
                        "previous_replicas": 1,
                        "reason": "Increase replicas after repeated crash evidence.",
                    },
                    "escalation_reason": None,
                }
            )
        }
    }
    monkeypatch.setattr(planner, "urlopen", lambda req, timeout=20: _FakeOllamaResponse(ollama_payload))

    out = lg.run_incident_langgraph("INC-001", approved=False)

    assert out["planner_backend"] == "ollama"
    assert out["diagnosis"] == "CrashLoopBackOff"
    assert out["confirmed"] is True
    assert out["proposed_action"]["action_type"] == "gitops_scale_deployment"
    assert out["planned_commands"]
    reset_settings_cache()


def test_ollama_guardrail_recovers_service503_from_escalation(monkeypatch) -> None:
    monkeypatch.setenv("SRE_AGENT_PLANNER_PROVIDER", "ollama")
    reset_settings_cache()
    MockMCP.reset_live_state()
    monkeypatch.setattr(lg, "retrieve_runbook_chunks", _fake_retrieval_for_query)

    monkeypatch.setattr(
        planner,
        "_call_ollama",
        lambda state: planner.AgentDecision(
            thought_summary="Data is unclear. Escalate.",
            hypothesis="Service503",
            confidence=0.2,
            decision="escalate",
            tool_name=None,
            proposed_action=None,
            escalation_reason="Insufficient evidence.",
        ),
    )

    out = lg.run_incident_langgraph("INC-002", approved=False)

    assert out["planner_backend"] == "ollama_guardrail"
    assert out["diagnosis"] == "Service503"
    assert out["confirmed"] is True
    assert out["proposed_action"]["action_type"] == "restart_pod"
    assert out["planned_commands"]
    reset_settings_cache()


def test_ollama_guardrail_overrides_service503_conflicting_action(monkeypatch) -> None:
    monkeypatch.setenv("SRE_AGENT_PLANNER_PROVIDER", "ollama")
    reset_settings_cache()
    MockMCP.reset_live_state()
    monkeypatch.setattr(lg, "retrieve_runbook_chunks", _fake_retrieval_for_query)

    monkeypatch.setattr(
        planner,
        "_call_ollama",
        lambda state: planner.AgentDecision(
            thought_summary="503 evidence is clear. Scale the deployment.",
            hypothesis="Service503",
            confidence=0.93,
            decision="propose_action",
            tool_name=None,
            proposed_action=planner.PlannerAction(
                action_type="scale_deployment",
                target="api",
                namespace="prod",
                replicas=3,
                previous_replicas=1,
                reason="Add capacity after repeated 503s.",
            ),
            escalation_reason=None,
        ),
    )

    out = lg.run_incident_langgraph("INC-002", approved=False)

    assert out["planner_backend"] == "ollama_guardrail"
    assert out["diagnosis"] == "Service503"
    assert out["confirmed"] is True
    assert out["proposed_action"]["action_type"] == "restart_pod"
    assert out["planned_commands"][0] == "kubectl delete pod -n prod -l app=api"
    reset_settings_cache()


def test_ollama_guardrail_recovers_dnsfailure_from_escalation(monkeypatch) -> None:
    monkeypatch.setenv("SRE_AGENT_PLANNER_PROVIDER", "ollama")
    reset_settings_cache()
    MockMCP.reset_live_state()
    monkeypatch.setattr(lg, "retrieve_runbook_chunks", _fake_retrieval_for_query)

    monkeypatch.setattr(
        planner,
        "_call_ollama",
        lambda state: planner.AgentDecision(
            thought_summary="Signals are inconclusive. Escalate.",
            hypothesis="DNSFailure",
            confidence=0.15,
            decision="escalate",
            tool_name=None,
            proposed_action=None,
            escalation_reason="Not enough evidence.",
        ),
    )

    out = lg.run_incident_langgraph("INC-003", approved=False)

    assert out["planner_backend"] == "ollama_guardrail"
    assert out["diagnosis"] == "DNSFailure"
    assert out["confirmed"] is True
    assert out["proposed_action"]["action_type"] == "restart_coredns"
    assert out["planned_commands"]
    reset_settings_cache()


def test_ollama_failure_enters_cooldown(monkeypatch) -> None:
    monkeypatch.setenv("SRE_AGENT_PLANNER_PROVIDER", "ollama")
    monkeypatch.setenv("SRE_AGENT_PLANNER_FAILURE_COOLDOWN_SECONDS", "60")
    reset_settings_cache()
    monkeypatch.setattr(lg, "retrieve_runbook_chunks", _fake_retrieval_for_query)

    def _boom(state):
        raise RuntimeError("ollama unavailable")

    monkeypatch.setattr(planner, "_call_ollama", _boom)

    first = lg.run_incident_langgraph("INC-002", approved=False)
    second = lg.run_incident_langgraph("INC-002", approved=False)

    assert first["planner_backend"] == "ollama_fallback"
    assert second["planner_backend"] == "ollama_cooldown_fallback"
    reset_settings_cache()
