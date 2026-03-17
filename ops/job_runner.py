from __future__ import annotations

from typing import Any, Dict

from agent.langgraph_agent import run_incident_langgraph
from mcp_tools.mock_mcp import MockMCP
from ops.runtime import run_with_retry
from ops.settings import get_settings
from ops.storage import log_audit_event, mark_run_status, update_execution_result, update_plan_result


def reset_mock_state_if_needed(incident_id: str) -> None:
    if get_settings().mcp_backend != "mock":
        return
    MockMCP.reset_live_state(incident_id)


def run_plan_job(*, run_id: str, incident_id: str, actor: str) -> None:
    mark_run_status(run_id, "planning")
    try:
        result = run_with_retry(
            lambda: run_incident_langgraph(
                incident_id=incident_id,
                approved=False,
                execution_mode="preview",
                tool_mode="mcp",
                run_id=run_id,
                save_artifacts_enabled=True,
            ),
            retries=1,
            timeout_seconds=get_settings().request_timeout_seconds,
            operation_name="plan_run",
        )
    except Exception as exc:
        mark_run_status(run_id, "failed")
        log_audit_event(run_id, "plan_failed", {"error": str(exc)}, actor=actor)
        return

    update_plan_result(run_id, result)
    log_audit_event(
        run_id,
        "plan_completed",
        {
            "diagnosis": result.get("diagnosis"),
            "confirmed": result.get("confirmed"),
            "requires_human_approval": result.get("requires_human_approval"),
            "latency_metrics": result.get("latency_metrics", {}),
            "retrieval_quality": result.get("retrieval_quality", {}),
        },
        actor=actor,
    )


def run_execute_job(*, run_id: str, incident_id: str, execution_mode: str, tool_mode: str, actor: str) -> None:
    mark_run_status(run_id, "executing")
    try:
        reset_mock_state_if_needed(incident_id)
        result = run_with_retry(
            lambda: run_incident_langgraph(
                incident_id=incident_id,
                approved=True,
                execution_mode=execution_mode,
                tool_mode=tool_mode,
                run_id=run_id,
                save_artifacts_enabled=True,
            ),
            retries=0,
            timeout_seconds=get_settings().execution_request_timeout_seconds,
            operation_name="execute_run",
        )
    except Exception as exc:
        mark_run_status(run_id, "execution_failed")
        log_audit_event(run_id, "execution_failed", {"error": str(exc)}, actor=actor)
        return

    mark_run_status(run_id, "verifying")
    update_execution_result(run_id, result)
    log_audit_event(
        run_id,
        "execution_result_persisted",
        {
            "executor_transport": result.get("executor_transport", ""),
            "improved": result.get("improved", False),
            "verification": result.get("verification", {}),
            "latency_metrics": result.get("latency_metrics", {}),
        },
        actor=actor,
    )


def dispatch_job(job: Dict[str, Any]) -> None:
    job_type = str(job.get("job_type", ""))
    payload = dict(job.get("payload") or {})
    if job_type == "plan":
        run_plan_job(
            run_id=str(payload["run_id"]),
            incident_id=str(payload["incident_id"]),
            actor=str(payload.get("actor", "system")),
        )
        return
    if job_type == "execute":
        run_execute_job(
            run_id=str(payload["run_id"]),
            incident_id=str(payload["incident_id"]),
            execution_mode=str(payload.get("execution_mode", "preview")),
            tool_mode=str(payload.get("tool_mode", "mcp")),
            actor=str(payload.get("actor", "system")),
        )
        return
    raise ValueError(f"Unknown job type: {job_type}")
