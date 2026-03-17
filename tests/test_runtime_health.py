from __future__ import annotations

import importlib
from pathlib import Path

from fastapi.testclient import TestClient

import ops.api as api_module
from agent.model_runtime import reset_model_runtime_state
from mcp_tools.mock_mcp import MockMCP
from ops.db import init_db, reset_db_state
from ops.settings import reset_settings_cache


def _configure_test_environment(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "runtime_health.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("PROMETHEUS_BASE_URL", raising=False)
    monkeypatch.delenv("LOKI_BASE_URL", raising=False)
    monkeypatch.delenv("KUBECONFIG", raising=False)
    monkeypatch.delenv("EXECUTOR_BASE_URL", raising=False)
    monkeypatch.delenv("OPS_API_TOKEN", raising=False)
    monkeypatch.setenv("OPS_API_TOKENS", "viewer-token|viewer|Viewer")
    monkeypatch.setenv("SRE_AGENT_PLANNER_PROVIDER", "deterministic")
    reset_model_runtime_state()
    reset_settings_cache()
    reset_db_state()
    init_db()
    MockMCP.reset_live_state()


def _headers(token: str) -> dict[str, str]:
    return {"X-API-Token": token}


def test_runtime_health_endpoint_reports_passive_status(monkeypatch, tmp_path: Path) -> None:
    _configure_test_environment(monkeypatch, tmp_path)
    api = importlib.reload(api_module)
    client = TestClient(api.app)

    response = client.get("/platform/runtime-health", headers=_headers("viewer-token"))
    assert response.status_code == 200
    payload = response.json()

    assert payload["planner"]["status"] == "disabled"
    assert payload["live_backends"]["configured"] is False
    assert payload["live_backends"]["components"]["prometheus"]["status"] == "not_configured"
    assert payload["live_backends"]["components"]["kubectl"]["status"] == "not_configured"
