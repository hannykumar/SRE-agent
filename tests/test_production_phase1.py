from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict, List

from fastapi.testclient import TestClient

import agent.langgraph_agent as lg
import ops.api as api_module
from executor.schemas import ExecutionRequest
from executor.service import ExecutionService
from mcp_tools.mock_mcp import MockMCP
from ops.db import init_db, reset_db_state
from ops.settings import reset_settings_cache
from ops.storage import get_execution_by_id, get_latest_rollback, list_audit_events


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


def _configure_test_environment(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "phase1.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("EXECUTOR_BASE_URL", raising=False)
    monkeypatch.delenv("OPS_API_TOKEN", raising=False)
    monkeypatch.setenv(
        "OPS_API_TOKENS",
        "viewer-token|viewer|Viewer,operator-token|operator|Operator,approver-token|approver|Approver,admin-token|admin|Admin",
    )
    monkeypatch.delenv("EXECUTOR_SHARED_TOKEN", raising=False)
    monkeypatch.setenv("SRE_AGENT_PLANNER_PROVIDER", "deterministic")
    reset_settings_cache()
    reset_db_state()
    init_db()
    MockMCP.reset_live_state()


def _headers(token: str) -> dict[str, str]:
    return {"X-API-Token": token}


def test_executor_service_records_execution_and_rollback(monkeypatch, tmp_path: Path) -> None:
    _configure_test_environment(monkeypatch, tmp_path)

    incident = MockMCP("INC-003").data
    request = ExecutionRequest(
        execution_id="exec-phase1",
        run_id="run-phase1",
        incident_id="INC-003",
        incident=incident,
        action={
            "action_type": "restart_coredns",
            "target": "coredns",
            "namespace": "kube-system",
            "reason": "DNS failures confirmed",
        },
        execution_mode="simulate",
        tool_mode="mcp",
        evidence_before={
            "metrics": incident.get("metrics", {}),
            "logs_tail": incident.get("logs_tail", []),
        },
        rollback_commands=["# rollback: CoreDNS restart is non-reversible; monitor cluster DNS health"],
    )

    out = ExecutionService().execute(request)

    assert out["execution_results"]
    assert out["rollback_record"]["status"] == "ready"
    assert get_execution_by_id("exec-phase1") is not None
    rollback = get_latest_rollback("run-phase1")
    assert rollback is not None
    assert "CoreDNS" in rollback["rollback_command"]


def test_api_plan_and_execute_use_database_storage(monkeypatch, tmp_path: Path) -> None:
    _configure_test_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(lg, "retrieve_runbook_chunks", _fake_retrieval)

    api = importlib.reload(api_module)
    client = TestClient(api.app)

    plan_response = client.post(
        "/runs/plan",
        json={"incident_id": "INC-001"},
        headers=_headers("operator-token"),
    )
    assert plan_response.status_code == 200
    plan_payload = plan_response.json()
    run_id = plan_payload["run_id"]
    assert plan_payload["status"] == "awaiting_approval"
    assert plan_payload["plan_result"]["diagnosis"] == "CrashLoopBackOff"
    assert plan_payload["request"]["execution_mode"] == "preview"
    assert plan_payload["request"]["tool_mode"] == "mcp"

    execute_response = client.post(f"/runs/{run_id}/approve-execute", headers=_headers("admin-token"))
    assert execute_response.status_code == 200
    execute_payload = execute_response.json()
    assert execute_payload["status"] == "completed"
    assert execute_payload["execute_result"]["execution_results"]

    detail_response = client.get(f"/runs/{run_id}", headers=_headers("viewer-token"))
    assert detail_response.status_code == 200
    assert detail_response.json()["status"] == "completed"

    audit_response = client.get(f"/runs/{run_id}/audit", headers=_headers("viewer-token"))
    assert audit_response.status_code == 200
    event_types = [event["event_type"] for event in audit_response.json()["events"]]
    assert "run_created" in event_types
    assert "plan_completed" in event_types
    assert "execution_result_persisted" in event_types

    rollback_response = client.get(f"/runs/{run_id}/rollback", headers=_headers("viewer-token"))
    assert rollback_response.status_code == 200
    assert rollback_response.json()["rollback"]["rollback_command"]

    # Storage-backed audit retrieval should match the API payload.
    assert len(list_audit_events(run_id)) >= 3


def test_approve_execute_can_override_loaded_run_to_live(monkeypatch, tmp_path: Path) -> None:
    _configure_test_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(lg, "retrieve_runbook_chunks", _fake_retrieval)

    api = importlib.reload(api_module)
    client = TestClient(api.app)

    plan_response = client.post(
        "/runs/plan",
        json={"incident_id": "INC-001"},
        headers=_headers("operator-token"),
    )
    assert plan_response.status_code == 200
    run_id = plan_response.json()["run_id"]
    assert plan_response.json()["request"]["execution_mode"] == "preview"

    execute_response = client.post(
        f"/runs/{run_id}/approve-execute",
        json={"execution_mode": "live"},
        headers=_headers("admin-token"),
    )
    assert execute_response.status_code == 200
    body = execute_response.json()
    assert body["request"]["execution_mode"] == "live"
    assert body["execute_result"]["execution_mode"] == "live"


def test_api_rbac_blocks_unauthorized_actions(monkeypatch, tmp_path: Path) -> None:
    _configure_test_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(lg, "retrieve_runbook_chunks", _fake_retrieval)

    api = importlib.reload(api_module)
    client = TestClient(api.app)

    viewer_plan = client.post("/runs/plan", json={"incident_id": "INC-001"}, headers=_headers("viewer-token"))
    assert viewer_plan.status_code == 403

    operator_plan = client.post("/runs/plan", json={"incident_id": "INC-001"}, headers=_headers("operator-token"))
    assert operator_plan.status_code == 200
    run_id = operator_plan.json()["run_id"]

    operator_approve = client.post(f"/runs/{run_id}/approve-execute", headers=_headers("operator-token"))
    assert operator_approve.status_code == 403

    approver_approve = client.post(f"/runs/{run_id}/approve-execute", headers=_headers("approver-token"))
    assert approver_approve.status_code == 200

    missing_token = client.get(f"/runs/{run_id}")
    assert missing_token.status_code == 401
