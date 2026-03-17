from __future__ import annotations

from typing import Any, Dict

from executor.engine import execute_action, execute_rollback
from executor.schemas import ExecutionRequest
from mcp_tools.actions import rollback_action_for_action
from ops.storage import (
    get_execution_by_id,
    log_audit_event,
    record_execution,
    record_rollback,
    update_latest_rollback_status,
)


class ExecutionService:
    def execute(self, request: ExecutionRequest) -> Dict[str, Any]:
        existing = get_execution_by_id(request.execution_id)
        if existing is not None:
            return existing

        result = execute_action(
            run_id=request.run_id,
            execution_mode=request.execution_mode,
            tool_mode=request.tool_mode,
            incident_id=request.incident_id,
            incident=request.incident,
            action=request.action,
            diagnosis=request.diagnosis,
            evidence_before=request.evidence_before,
        )
        command = str(result.get("command", ""))
        rollback_command = request.rollback_commands[0] if request.rollback_commands else ""

        payload: Dict[str, Any] = {
            "execution_id": request.execution_id,
            "status": str(result.get("status", "completed")),
            "execution_results": result.get("execution_results", []),
            "evidence_after": result.get("evidence_after", {}),
            "improved": bool(result.get("improved", False)),
            "improvement_summary": str(result.get("improvement_summary", "")),
            "verification": result.get("verification", {}),
            "rollback_record": {
                "status": "ready" if rollback_command else "not_available",
                "rollback_command": rollback_command,
            },
            "executor_transport": "service",
        }

        record_execution(
            execution_id=request.execution_id,
            run_id=request.run_id,
            execution_mode=request.execution_mode,
            command=command,
            action=request.action,
            result_payload=payload,
        )
        if rollback_command:
            record_rollback(
                execution_id=request.execution_id,
                run_id=request.run_id,
                rollback_command=rollback_command,
                payload={
                    "action": request.action,
                    "command": rollback_command,
                    "rollback_action": rollback_action_for_action(request.action, request.incident),
                },
            )
        if request.run_id:
            log_audit_event(
                request.run_id,
                "execution_completed",
                {
                    "execution_id": request.execution_id,
                    "execution_mode": request.execution_mode,
                    "command": command,
                    "improved": payload["improved"],
                },
                actor="executor",
            )
        return payload

    def rollback(self, request: ExecutionRequest) -> Dict[str, Any]:
        result = execute_rollback(
            run_id=request.run_id,
            execution_mode=request.execution_mode,
            tool_mode=request.tool_mode,
            incident_id=request.incident_id,
            incident=request.incident,
            action=request.action,
        )
        if request.run_id:
            update_latest_rollback_status(
                request.run_id,
                status="executed" if result.get("execution_results") else "not_available",
                payload={
                    "rollback_result": result,
                    "action": request.action,
                },
            )
            log_audit_event(
                request.run_id,
                "rollback_executed",
                {
                    "execution_mode": request.execution_mode,
                    "command": result.get("command", ""),
                },
                actor="executor",
            )
        return {
            "execution_id": request.execution_id,
            "status": str(result.get("status", "completed")),
            "execution_results": result.get("execution_results", []),
            "evidence_after": result.get("evidence_after", {}),
            "improved": bool(result.get("improved", False)),
            "improvement_summary": str(result.get("improvement_summary", "")),
            "verification": result.get("verification", {}),
            "rollback_record": {"status": "executed", "rollback_command": result.get("command", "")},
            "executor_transport": "service",
        }
