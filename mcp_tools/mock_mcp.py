from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp_tools.actions import normalize_action, render_action_to_command
from mcp_tools.service_context import dashboards, incident_history, owner, recent_deploys, trace_summary

MOCK_INCIDENTS_DIR = Path("mcp_mock_data/incidents")
LIVE_STATE_DIR = Path("data/mock_live_state")


class MockMCP:
    """
    Mock MCP surface for the agent.

    Read tools return stable fake cluster data.
    Write actions are routed through execute_remediation().
    """

    # Class-level store so multiple instances see the same evolving incident state
    _LIVE_STATE: Dict[str, Dict[str, Any]] = {}

    def __init__(self, incident_id: str, trace: Optional[object] = None, allow_write: bool = False):
        self.incident_id = incident_id
        self.trace = trace
        self.allow_write = allow_write

        # Initialize live state once (deep copy so we can safely mutate)
        if incident_id not in self._LIVE_STATE:
            self._LIVE_STATE[incident_id] = copy.deepcopy(self._load_live_state(incident_id))

        # Always read/write from live state
        self.data = self._LIVE_STATE[incident_id]

    # -----------------------
    # Step 10: reset helpers
    # -----------------------
    @classmethod
    def reset_live_state(cls, incident_id: Optional[str] = None) -> None:
        """
        Reset the in-memory "cluster state".

        - If incident_id is None: clears ALL incident live states.
        - If incident_id is set: clears only that incident's live state.
        """
        if incident_id is None:
            cls._LIVE_STATE.clear()
            if LIVE_STATE_DIR.exists():
                for fp in LIVE_STATE_DIR.glob("*.json"):
                    fp.unlink()
        else:
            cls._LIVE_STATE.pop(incident_id, None)
            state_path = LIVE_STATE_DIR / f"{incident_id}.json"
            if state_path.exists():
                state_path.unlink()

    @classmethod
    def seed_live_state(cls, incident_id: str, payload: Dict[str, Any]) -> None:
        """
        Seed both in-memory and file-backed live state so local and MCP subprocess
        clients observe the same incident payload.
        """
        LIVE_STATE_DIR.mkdir(parents=True, exist_ok=True)
        seeded = copy.deepcopy(payload)
        cls._LIVE_STATE[incident_id] = seeded
        (LIVE_STATE_DIR / f"{incident_id}.json").write_text(json.dumps(seeded, indent=2), encoding="utf-8")

    # -----------------------
    # Load incident JSON
    # -----------------------
    def _load_incident(self, incident_id: str) -> Dict[str, Any]:
        for fp in MOCK_INCIDENTS_DIR.glob("*.json"):
            with open(fp, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("incident_id") == incident_id:
                return d
        raise FileNotFoundError(f"Incident {incident_id} not found in {MOCK_INCIDENTS_DIR}")

    def _state_path(self, incident_id: str) -> Path:
        LIVE_STATE_DIR.mkdir(parents=True, exist_ok=True)
        return LIVE_STATE_DIR / f"{incident_id}.json"

    def _load_live_state(self, incident_id: str) -> Dict[str, Any]:
        state_path = self._state_path(incident_id)
        if state_path.exists():
            return json.loads(state_path.read_text(encoding="utf-8"))
        data = self._load_incident(incident_id)
        state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data

    def _save_live_state(self) -> None:
        self._state_path(self.incident_id).write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    # -----------------------
    # Read tools
    # -----------------------
    def get_pod_status(self, service: str, namespace: str) -> Dict[str, Any]:
        return self.data["pod_status"]

    def describe_pod(self, pod_name: str, namespace: str) -> Dict[str, Any]:
        return self.data["pod_describe"]

    def get_metrics(self, service: str, namespace: str) -> Dict[str, Any]:
        return self.data["metrics"]

    def get_pod_logs(self, pod_name: str, namespace: str, lines: int = 200) -> List[str]:
        logs = self.data["logs_tail"]
        return logs[-min(lines, len(logs)) :]

    def get_cluster_events(self, namespace: str) -> List[str]:
        return self.data["cluster_events"]

    def query_prometheus(self, expression: str) -> Dict[str, Any]:
        metrics = self.data.get("metrics", {})
        metric_name = "synthetic_score"
        value: float | int | None = None
        lowered = str(expression).lower()
        if "error" in lowered:
            metric_name = "error_rate_percent"
            value = metrics.get("error_rate_percent", metrics.get("error_rate"))
        elif "dns" in lowered:
            metric_name = "dns_error_rate_percent"
            value = metrics.get("dns_error_rate_percent", metrics.get("dns_error_rate"))
        elif "memory" in lowered:
            metric_name = "memory_percent"
            value = metrics.get("memory_percent")
        elif "latency" in lowered or "p95" in lowered:
            metric_name = "p95_latency_ms"
            value = metrics.get("p95_latency_ms")
        elif "cpu" in lowered:
            metric_name = "cpu_percent"
            value = metrics.get("cpu_percent")
        return {"expression": expression, "metric_name": metric_name, "value": value}

    def query_loki(self, service: str, namespace: str, limit: int = 50) -> Dict[str, Any]:
        lines = self.get_pod_logs(self.data["pod_status"]["pod_name"], namespace=namespace, lines=limit)
        return {"query": f'{{service=\"{service}\",namespace=\"{namespace}\"}}', "lines": lines[-limit:]}

    def query_tempo(self, service: str, namespace: str, limit: int = 20) -> Dict[str, Any]:
        spans = trace_summary(service)[:limit]
        return {"service": service, "namespace": namespace, "spans": spans}

    def get_dashboard_context(self, service: str, namespace: str) -> Dict[str, Any]:
        panels = dashboards(service)
        dashboard = panels[0] if panels else {}
        return {
            "service": service,
            "namespace": namespace,
            "dashboard_uid": dashboard.get("uid", ""),
            "dashboard_title": dashboard.get("title", ""),
            "panels": dashboard.get("panels", []),
        }

    def get_recent_deploys(self, service: str, namespace: str) -> List[Dict[str, Any]]:
        return recent_deploys(service)

    def get_service_owner(self, service: str) -> Dict[str, Any]:
        return owner(service)

    def get_incident_history(self, service: str, limit: int = 10) -> List[Dict[str, Any]]:
        return incident_history(service)[:limit]

    def call_tool(self, tool_name: str, **kwargs: Any) -> Any:
        tool = getattr(self, tool_name, None)
        if tool is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        return tool(**kwargs)

    # -----------------------
    # Write tools (mocked)
    # -----------------------
    def _require_write_allowed(self) -> None:
        if not self.allow_write:
            raise PermissionError("Write actions are disabled. Set allow_write=True only after human approval.")

    def _scale_metric(self, keys: List[str], factor: float, floor: float = 0.0) -> None:
        metrics = self.data.get("metrics", {})
        for key in keys:
            if key in metrics:
                metrics[key] = max(floor, float(metrics[key]) * factor)

    def restart_pod(self, service: str, namespace: str) -> Dict[str, Any]:
        """
        Mock restart: simulate improvement by reducing error rate and clearing recent error logs.
        """
        self._require_write_allowed()

        # Increase restart count
        self.data["pod_status"]["restarts"] = int(self.data["pod_status"].get("restarts", 0)) + 1

        # Simulate: errors reduce after restart
        self._scale_metric(["error_rate", "error_rate_percent"], factor=0.3, floor=0.0)
        self._scale_metric(["dns_error_rate", "dns_error_rate_percent"], factor=0.5, floor=0.0)
        self._scale_metric(["p95_latency_ms"], factor=0.6, floor=50.0)
        self._scale_metric(["memory_percent"], factor=0.85, floor=1.0)
        self._scale_metric(["memory_rss_mb"], factor=0.9, floor=1.0)

        # Simulate: logs become healthier
        logs = self.data.get("logs_tail", [])
        filtered = [
            l
            for l in logs
            if "503" not in l
            and "NXDOMAIN" not in l
            and "could not resolve" not in l.lower()
            and "out of memory" not in l.lower()
            and "oom" not in l.lower()
        ]
        filtered.append("INFO: pod restarted successfully; health checks passing")
        self.data["logs_tail"] = filtered[-500:]
        self._save_live_state()

        return {"status": "ok", "action": "restart_pod", "service": service, "namespace": namespace}

    def scale_deployment(self, service: str, namespace: str, replicas: int) -> Dict[str, Any]:
        """
        Mock scale: simulate improvement by reducing saturation-like signals.
        """
        self._require_write_allowed()

        self.data["pod_status"]["replicas"] = replicas

        self._scale_metric(["cpu_percent"], factor=0.7, floor=5.0)
        self._scale_metric(["error_rate", "error_rate_percent"], factor=0.5, floor=0.0)
        self._scale_metric(["p95_latency_ms"], factor=0.8, floor=50.0)
        self._scale_metric(["memory_percent"], factor=0.9, floor=1.0)

        logs = self.data.get("logs_tail", [])
        logs.append(f"INFO: scaled deployment to replicas={replicas}")
        self.data["logs_tail"] = logs[-500:]
        self._save_live_state()

        return {
            "status": "ok",
            "action": "scale_deployment",
            "service": service,
            "namespace": namespace,
            "replicas": replicas,
        }

    def restart_coredns(self, namespace: str = "kube-system") -> Dict[str, Any]:
        """
        Mock CoreDNS restart: simulate DNS recovery.
        """
        self._require_write_allowed()

        self._scale_metric(["dns_error_rate", "dns_error_rate_percent"], factor=0.1, floor=0.0)
        self._scale_metric(["error_rate", "error_rate_percent"], factor=0.3, floor=0.0)

        logs = self.data.get("logs_tail", [])
        filtered = [l for l in logs if "nxdomain" not in l.lower() and "could not resolve" not in l.lower()]
        filtered.append("INFO: CoreDNS restarted; DNS resolution normal")
        self.data["logs_tail"] = filtered[-500:]
        self._save_live_state()

        return {"status": "ok", "action": "restart_coredns", "namespace": namespace}

    def execute_remediation(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a structured remediation action after approval.
        """
        self._require_write_allowed()

        normalized = normalize_action(action, self.data)
        command = render_action_to_command(normalized, self.data)
        action_type = normalized["action_type"]

        if action_type == "restart_pod":
            result = self.restart_pod(service=normalized["target"], namespace=normalized["namespace"])
        elif action_type in {"scale_deployment", "gitops_scale_deployment"}:
            result = self.scale_deployment(
                service=normalized["target"],
                namespace=normalized["namespace"],
                replicas=int(normalized["replicas"]),
            )
        else:
            result = self.restart_coredns(namespace=normalized["namespace"])

        return {
            "status": "ok",
            "tool": "execute_remediation",
            "command": command,
            "action": normalized,
            "result": result,
        }

    def noop(self, **kwargs: Any) -> Dict[str, Any]:
        return {"status": "noop", "details": kwargs}
