from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict, List

from fastapi.testclient import TestClient

import agent.langgraph_agent as lg
import ops.api as api_module
from mcp_tools.mock_mcp import MockMCP
from ops.db import init_db, reset_db_state
from ops.settings import reset_settings_cache


def _fake_retrieval(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    lowered = query.lower()
    if "oom" in lowered or "crashloop" in lowered:
        return [
            {
                "score": 1.0,
                "source_file": "01_crashloopbackoff_oomkilled.md",
                "incident_type": "CrashLoopBackOff",
                "section": "Symptoms",
                "text": "OOMKilled and CrashLoopBackOff observed",
            }
        ][:limit]
    if "dns" in lowered or "resolve" in lowered:
        return [
            {
                "score": 1.0,
                "source_file": "05_dns_resolution_failures.md",
                "incident_type": "DNSFailure",
                "section": "Symptoms",
                "text": "Resolver failures and NXDOMAIN errors indicate DNS issues.",
            }
        ][:limit]
    return [
        {
            "score": 1.0,
            "source_file": "02_service_503_upstream_timeout.md",
            "incident_type": "Service503",
            "section": "Symptoms",
            "text": "503 responses with upstream timeouts indicate dependency failure.",
        }
    ][:limit]


def _configure_test_environment(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "grafana_webhook.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("EXECUTOR_BASE_URL", raising=False)
    monkeypatch.delenv("OPS_API_TOKEN", raising=False)
    monkeypatch.setenv("OPS_API_TOKENS", "viewer-token|viewer|Viewer,operator-token|operator|Operator")
    monkeypatch.setenv("GRAFANA_WEBHOOK_TOKEN", "grafana-test-token")
    monkeypatch.setenv("SRE_AGENT_PLANNER_PROVIDER", "deterministic")
    reset_settings_cache()
    reset_db_state()
    init_db()
    MockMCP.reset_live_state()


def _firing_payload(
    *,
    alertname: str = "Demo API High 5xx Rate",
    summary: str = "Demo API is serving too many 5xx responses.",
    incident_id: str = "INC-002",
    scenario: str = "service503",
    starts_at: str = "2026-03-11T21:00:00Z",
) -> dict[str, Any]:
    return {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": alertname,
                    "service": "demo-api",
                    "severity": "warning",
                    "incident_id": incident_id,
                    "scenario": scenario,
                },
                "annotations": {
                    "summary": summary,
                },
                "startsAt": starts_at,
                "fingerprint": f"{incident_id}-{scenario}-{starts_at}",
            }
        ],
    }


def test_grafana_webhook_creates_plan_run(monkeypatch, tmp_path: Path) -> None:
    _configure_test_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(lg, "retrieve_runbook_chunks", _fake_retrieval)

    api = importlib.reload(api_module)
    client = TestClient(api.app)

    response = client.post("/alerts/grafana/webhook?token=grafana-test-token", json=_firing_payload())
    assert response.status_code == 200
    payload = response.json()

    assert payload["accepted"] is True
    assert payload["created"] is True
    assert payload["incident_id"] == "INC-002"
    assert payload["run"]["request"]["source"] == "grafana_webhook"
    assert payload["run"]["plan_result"]["diagnosis"] == "Service503"


def test_generic_alert_endpoint_creates_plan_run(monkeypatch, tmp_path: Path) -> None:
    _configure_test_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "generic-test-token")
    reset_settings_cache()
    monkeypatch.setattr(lg, "retrieve_runbook_chunks", _fake_retrieval)

    api = importlib.reload(api_module)
    client = TestClient(api.app)

    response = client.post(
        "/alerts/events?token=generic-test-token",
        json={
            "incident_id": "INC-002",
            "alertname": "Generic Upstream 503 Alert",
            "summary": "API is returning 503 responses from upstream dependencies.",
            "status": "firing",
            "service": "api",
            "severity": "warning",
        },
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["accepted"] is True
    assert payload["created"] is True
    assert payload["normalized"]["source_type"] == "generic"
    assert payload["run"]["request"]["source"] == "generic_alert"


def test_grafana_webhook_maps_crashloop_alert(monkeypatch, tmp_path: Path) -> None:
    _configure_test_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(lg, "retrieve_runbook_chunks", _fake_retrieval)

    api = importlib.reload(api_module)
    client = TestClient(api.app)

    response = client.post(
        "/alerts/grafana/webhook?token=grafana-test-token",
        json=_firing_payload(
            alertname="Demo API CrashLoop OOM Signal",
            summary="Demo service is emitting a CrashLoop/OOM signal.",
            incident_id="INC-001",
            scenario="crashloop",
        ),
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["incident_id"] == "INC-001"
    assert payload["run"]["plan_result"]["diagnosis"] == "CrashLoopBackOff"


def test_grafana_webhook_maps_dns_alert(monkeypatch, tmp_path: Path) -> None:
    _configure_test_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(lg, "retrieve_runbook_chunks", _fake_retrieval)

    api = importlib.reload(api_module)
    client = TestClient(api.app)

    response = client.post(
        "/alerts/grafana/webhook?token=grafana-test-token",
        json=_firing_payload(
            alertname="Demo API DNS Failure Signal",
            summary="Demo service is emitting a DNS failure signal.",
            incident_id="INC-003",
            scenario="dnsfailure",
        ),
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["incident_id"] == "INC-003"
    assert payload["run"]["plan_result"]["diagnosis"] == "DNSFailure"


def test_grafana_webhook_rejects_invalid_token(monkeypatch, tmp_path: Path) -> None:
    _configure_test_environment(monkeypatch, tmp_path)

    api = importlib.reload(api_module)
    client = TestClient(api.app)

    response = client.post("/alerts/grafana/webhook?token=wrong", json=_firing_payload())
    assert response.status_code == 401


def test_grafana_webhook_ignores_resolved_alert(monkeypatch, tmp_path: Path) -> None:
    _configure_test_environment(monkeypatch, tmp_path)

    api = importlib.reload(api_module)
    client = TestClient(api.app)

    payload = _firing_payload()
    payload["status"] = "resolved"
    payload["alerts"][0]["status"] = "resolved"
    response = client.post("/alerts/grafana/webhook?token=grafana-test-token", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["created"] is False
    assert body["reason"] == "resolved_alert"


def test_grafana_webhook_deduplicates_active_alert(monkeypatch, tmp_path: Path) -> None:
    _configure_test_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(lg, "retrieve_runbook_chunks", _fake_retrieval)

    api = importlib.reload(api_module)
    client = TestClient(api.app)

    first = client.post("/alerts/grafana/webhook?token=grafana-test-token", json=_firing_payload())
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["created"] is True

    second = client.post("/alerts/grafana/webhook?token=grafana-test-token", json=_firing_payload())
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["accepted"] is True
    assert second_body["created"] is False
    assert second_body["reason"] == "duplicate_alert"
    assert second_body["active_run_id"] == first_body["run_id"]


def test_grafana_webhook_creates_new_run_for_new_alert_occurrence(monkeypatch, tmp_path: Path) -> None:
    _configure_test_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(lg, "retrieve_runbook_chunks", _fake_retrieval)

    api = importlib.reload(api_module)
    client = TestClient(api.app)

    first = client.post(
        "/alerts/grafana/webhook?token=grafana-test-token",
        json=_firing_payload(starts_at="2026-03-11T21:00:00Z"),
    )
    assert first.status_code == 200
    first_run = first.json()["run_id"]

    resolved = _firing_payload(starts_at="2026-03-11T21:00:00Z")
    resolved["status"] = "resolved"
    resolved["alerts"][0]["status"] = "resolved"
    resolved_response = client.post("/alerts/grafana/webhook?token=grafana-test-token", json=resolved)
    assert resolved_response.status_code == 200
    assert resolved_response.json()["reason"] == "resolved_alert"
    assert resolved_response.json()["resolved_runs"] == 1

    second = client.post(
        "/alerts/grafana/webhook?token=grafana-test-token",
        json=_firing_payload(starts_at="2026-03-11T21:10:00Z"),
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["created"] is True
    assert second_body["run_id"] != first_run


def test_plan_run_resets_mock_incident_state(monkeypatch, tmp_path: Path) -> None:
    _configure_test_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(lg, "retrieve_runbook_chunks", _fake_retrieval)

    # Simulate a previous remediation mutating the in-memory/file-backed mock state.
    mock = MockMCP("INC-002", allow_write=True)
    mock.restart_pod(service="api", namespace="prod")

    api = importlib.reload(api_module)
    client = TestClient(api.app)

    response = client.post(
        "/runs/plan",
        json={"incident_id": "INC-002", "execution_mode": "preview"},
        headers={"X-API-Token": "operator-token"},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["plan_result"]["diagnosis"] == "Service503"
