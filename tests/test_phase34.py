from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Dict, List

from fastapi.testclient import TestClient

import agent.langgraph_agent as lg
import ops.api as api_module
from executor.engine import execute_action
from mcp_tools.live_backends import KubectlLiveAdapter, PrometheusAdapter
from mcp_tools.mock_mcp import MockMCP
from ops.db import init_db, reset_db_state
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


def _configure_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'phase34.db'}")
    monkeypatch.setenv(
        "OPS_API_TOKENS",
        "viewer-token|viewer|Viewer,operator-token|operator|Operator,approver-token|approver|Approver,admin-token|admin|Admin",
    )
    monkeypatch.setenv("GITOPS_REPO_DIR", str(tmp_path / "gitops_repo"))
    monkeypatch.setenv("SRE_MCP_BACKEND", "mock")
    monkeypatch.setenv("SRE_AGENT_PLANNER_PROVIDER", "deterministic")
    monkeypatch.delenv("EXECUTOR_BASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    reset_settings_cache()
    reset_db_state()
    init_db()
    MockMCP.reset_live_state()


def _headers(token: str) -> dict[str, str]:
    return {"X-API-Token": token}


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any]):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_prometheus_adapter_parses_metrics(monkeypatch, tmp_path: Path) -> None:
    _configure_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PROMETHEUS_BASE_URL", "http://prometheus.local")
    reset_settings_cache()

    def fake_urlopen(_url: str, timeout: int = 15):
        return _FakeResponse({"status": "success", "data": {"result": [{"value": [0, "12.5"]}]}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    metrics = PrometheusAdapter().get_metrics(service="checkout", namespace="prod")

    assert metrics["error_rate_percent"] == 12.5
    assert metrics["p95_latency_ms"] == 12.5


def test_kubectl_live_adapter_parses_pod_status(monkeypatch, tmp_path: Path) -> None:
    _configure_environment(monkeypatch, tmp_path)

    class Completed:
        def __init__(self, stdout: str):
            self.stdout = stdout

    def fake_run(cmd: list[str], check: bool, capture_output: bool, text: bool):
        if "pods" in cmd:
            return Completed(
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "checkout-abc", "labels": {"replicas": "2"}},
                                "status": {
                                    "phase": "Running",
                                    "containerStatuses": [{"restartCount": 4, "state": {"waiting": {"reason": "CrashLoopBackOff"}}}],
                                },
                            }
                        ]
                    }
                )
            )
        return Completed(json.dumps({}))

    monkeypatch.setattr("subprocess.run", fake_run)

    payload = KubectlLiveAdapter().get_pod_status(service="checkout", namespace="prod")

    assert payload["pod_name"] == "checkout-abc"
    assert payload["restarts"] == 4
    assert payload["replicas"] == 2


def test_gitops_rollback_flow(monkeypatch, tmp_path: Path) -> None:
    _configure_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(lg, "retrieve_runbook_chunks", _fake_retrieval)

    api = importlib.reload(api_module)
    client = TestClient(api.app)

    plan_response = client.post("/runs/plan", json={"incident_id": "INC-001"}, headers=_headers("operator-token"))
    assert plan_response.status_code == 200
    plan_payload = plan_response.json()
    run_id = plan_payload["run_id"]
    assert plan_payload["plan_result"]["proposed_action"]["action_type"] == "gitops_scale_deployment"

    approve_response = client.post(f"/runs/{run_id}/approve-execute", headers=_headers("admin-token"))
    assert approve_response.status_code == 200
    rollback_response = client.post(f"/runs/{run_id}/rollback-execute", headers=_headers("admin-token"))
    assert rollback_response.status_code == 200
    rollback_payload = rollback_response.json()["rollback_result"]
    assert rollback_payload["execution_results"]
    assert rollback_payload["execution_results"][0]["tool"] == "gitops_rollback"

    gitops_repo = tmp_path / "gitops_repo"
    change_dir = gitops_repo / "_changes" / run_id / "rollback"
    assert change_dir.exists()


def test_live_alert_lab_execution_verifies_resolution(monkeypatch, tmp_path: Path) -> None:
    _configure_environment(monkeypatch, tmp_path)
    incident = MockMCP("INC-002").data

    class _Client:
        def execute_remediation(self, action: Dict[str, Any]) -> Dict[str, Any]:
            return {"status": "ok", "tool": "execute_remediation", "action": action}

    monkeypatch.setattr("executor.engine.get_tool_client", lambda tool_mode, incident_id, allow_write=False: _Client())
    monkeypatch.setattr(
        "executor.engine.collect_evidence",
        lambda tool_mode, incident_id, incident: {
            "metrics": {"error_rate_percent": 0.0},
            "logs_tail": ["INFO service healthy"],
        },
    )
    monkeypatch.setattr("executor.engine._reset_demo_service", lambda: {"mode": "ok"})
    monkeypatch.setattr(
        "executor.engine.verify_alert_resolution",
        lambda incident_id: {
            "enabled": True,
            "resolved": True,
            "incident_id": incident_id,
            "alert_state": "Normal",
            "metric_value": 0.0,
            "metric_threshold": 0.2,
        },
    )

    out = execute_action(
        run_id="run-live-lab",
        execution_mode="live",
        tool_mode="mcp",
        incident_id="INC-002",
        incident=incident,
        action={
            "action_type": "restart_pod",
            "target": "api",
            "namespace": "prod",
            "reason": "Clear the failing pod in the alert lab.",
        },
        evidence_before={
            "metrics": {"error_rate_percent": 18},
            "logs_tail": incident.get("logs_tail", []),
        },
    )

    assert out["improved"] is True
    assert out["verification"]["resolved"] is True
    assert any(result.get("tool") == "alert_lab_adapter" for result in out["execution_results"])
