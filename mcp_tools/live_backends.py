from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from ops.settings import get_settings
from mcp_tools.service_context import dashboards, incident_history, owner, recent_deploys, trace_summary


def _safe_json_load(raw: str) -> Dict[str, Any]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError("Expected a JSON object from backend")
    return payload


class KubectlLiveAdapter:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _base_cmd(self) -> List[str]:
        cmd = [self.settings.kubectl_binary]
        if self.settings.kubeconfig_path:
            cmd.extend(["--kubeconfig", self.settings.kubeconfig_path])
        if self.settings.kubectl_context:
            cmd.extend(["--context", self.settings.kubectl_context])
        return cmd

    def _run(self, *args: str) -> str:
        completed = subprocess.run(
            [*self._base_cmd(), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout

    def get_pod_status(self, service: str, namespace: str) -> Dict[str, Any]:
        raw = self._run("get", "pods", "-n", namespace, "-l", f"app={service}", "-o", "json")
        data = _safe_json_load(raw)
        items = data.get("items", [])
        if not items:
            raise RuntimeError(f"No pods found for app={service} in namespace={namespace}")

        pod = items[0]
        statuses = pod.get("status", {}).get("containerStatuses", [])
        state = statuses[0].get("state", {}) if statuses else {}
        waiting_reason = state.get("waiting", {}).get("reason") if isinstance(state, dict) else None
        return {
            "pod_name": pod.get("metadata", {}).get("name", ""),
            "phase": pod.get("status", {}).get("phase", "Unknown"),
            "restarts": sum(int(status.get("restartCount", 0) or 0) for status in statuses),
            "reason": waiting_reason or pod.get("status", {}).get("reason", "Running"),
            "replicas": int(pod.get("metadata", {}).get("labels", {}).get("replicas", 1) or 1),
        }

    def describe_pod(self, pod_name: str, namespace: str) -> Dict[str, Any]:
        raw = self._run("get", "pod", pod_name, "-n", namespace, "-o", "json")
        pod = _safe_json_load(raw)
        statuses = pod.get("status", {}).get("containerStatuses", [])
        last_state = statuses[0].get("lastState", {}) if statuses else {}
        resources = pod.get("spec", {}).get("containers", [{}])[0].get("resources", {})
        return {
            "last_state": last_state,
            "limits": resources.get("limits", {}),
            "requests": resources.get("requests", {}),
        }

    def get_pod_logs(self, pod_name: str, namespace: str, lines: int = 200) -> List[str]:
        raw = self._run("logs", pod_name, "-n", namespace, f"--tail={int(lines)}")
        return [line for line in raw.splitlines() if line.strip()]

    def get_cluster_events(self, namespace: str) -> List[str]:
        raw = self._run("get", "events", "-n", namespace, "--sort-by=.lastTimestamp", "-o", "json")
        data = _safe_json_load(raw)
        return [
            f"{item.get('type', 'Normal')} {item.get('reason', '')} {item.get('message', '')}".strip()
            for item in data.get("items", [])[-20:]
        ]


class PrometheusAdapter:
    def __init__(self) -> None:
        self.settings = get_settings()
        if not self.settings.prometheus_base_url:
            raise RuntimeError("PROMETHEUS_BASE_URL is required for the live metrics backend")

    def _query(self, expression: str) -> float | None:
        encoded = urllib.parse.urlencode({"query": expression})
        url = f"{self.settings.prometheus_base_url.rstrip('/')}/api/v1/query?{encoded}"
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = _safe_json_load(response.read().decode("utf-8"))
        result = payload.get("data", {}).get("result", [])
        if not result:
            return None
        value = result[0].get("value", [None, None])[1]
        return float(value) if value is not None else None

    def get_metrics(self, service: str, namespace: str) -> Dict[str, Any]:
        filters = f'namespace="{namespace}",service="{service}"'
        metrics = {
            "error_rate_percent": self._query(
                f'(sum(rate(http_requests_total{{{filters},status=~"5.."}}[5m])) / clamp_min(sum(rate(http_requests_total{{{filters}}}[5m])), 0.001)) * 100'
            ),
            "p95_latency_ms": self._query(
                f'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{{{filters}}}[5m])) by (le)) * 1000'
            ),
            "cpu_percent": self._query(
                f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}",pod=~"{service}.*"}}[5m])) * 100'
            ),
            "memory_percent": self._query(
                f'(sum(container_memory_working_set_bytes{{namespace="{namespace}",pod=~"{service}.*"}}) / clamp_min(sum(kube_pod_container_resource_limits{{namespace="{namespace}",pod=~"{service}.*",resource="memory"}}), 1)) * 100'
            ),
            "dns_error_rate_percent": self._query(
                '(sum(rate(coredns_dns_responses_total{rcode=~"NXDOMAIN|SERVFAIL"}[5m])) / clamp_min(sum(rate(coredns_dns_responses_total[5m])), 0.001)) * 100'
            ),
        }
        return {key: value for key, value in metrics.items() if value is not None}


class LokiAdapter:
    def __init__(self) -> None:
        self.settings = get_settings()
        if not self.settings.loki_base_url:
            raise RuntimeError("LOKI_BASE_URL is required when SRE_LOGS_BACKEND=loki")

    def get_pod_logs(self, pod_name: str, namespace: str, lines: int = 200) -> List[str]:
        query = f'{{namespace="{namespace}", pod="{pod_name}"}}'
        encoded = urllib.parse.urlencode({"query": query, "limit": int(lines), "direction": "BACKWARD"})
        url = f"{self.settings.loki_base_url.rstrip('/')}/loki/api/v1/query_range?{encoded}"
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = _safe_json_load(response.read().decode("utf-8"))
        streams = payload.get("data", {}).get("result", [])
        lines_out: List[str] = []
        for stream in streams:
            for _, line in stream.get("values", []):
                lines_out.append(str(line))
        return lines_out[-int(lines) :]


class LiveSREBackend:
    def __init__(
        self,
        kubectl: KubectlLiveAdapter | None = None,
        metrics: PrometheusAdapter | None = None,
        logs: LokiAdapter | None = None,
    ) -> None:
        self.settings = get_settings()
        self.kubectl = kubectl or KubectlLiveAdapter()
        self.metrics = metrics
        self.logs = logs
        if self.settings.prometheus_base_url:
            self.metrics = metrics or PrometheusAdapter()
        if self.settings.logs_backend == "loki" and self.settings.loki_base_url:
            self.logs = logs or LokiAdapter()

    def _require_metrics(self) -> PrometheusAdapter:
        if self.metrics is None:
            raise RuntimeError("PROMETHEUS_BASE_URL is required for the live metrics backend")
        return self.metrics

    def get_pod_status(self, service: str, namespace: str) -> Dict[str, Any]:
        return self.kubectl.get_pod_status(service=service, namespace=namespace)

    def describe_pod(self, pod_name: str, namespace: str) -> Dict[str, Any]:
        return self.kubectl.describe_pod(pod_name=pod_name, namespace=namespace)

    def get_metrics(self, service: str, namespace: str) -> Dict[str, Any]:
        return self._require_metrics().get_metrics(service=service, namespace=namespace)

    def get_pod_logs(self, pod_name: str, namespace: str, lines: int = 200) -> List[str]:
        if self.logs is not None:
            return self.logs.get_pod_logs(pod_name=pod_name, namespace=namespace, lines=lines)
        return self.kubectl.get_pod_logs(pod_name=pod_name, namespace=namespace, lines=lines)

    def get_cluster_events(self, namespace: str) -> List[str]:
        return self.kubectl.get_cluster_events(namespace=namespace)

    def query_prometheus(self, expression: str) -> Dict[str, Any]:
        metrics = self._require_metrics()
        return {
            "expression": expression,
            "value": metrics._query(expression),
        }

    def query_loki(self, service: str, namespace: str, limit: int = 50) -> Dict[str, Any]:
        pod = self.get_pod_status(service=service, namespace=namespace)
        lines = self.get_pod_logs(pod_name=pod["pod_name"], namespace=namespace, lines=limit)
        return {"query": f'{{service=\"{service}\",namespace=\"{namespace}\"}}', "lines": lines[-limit:]}

    def query_tempo(self, service: str, namespace: str, limit: int = 20) -> Dict[str, Any]:
        tempo_base_url = os.getenv("TEMPO_BASE_URL", "").strip()
        if not tempo_base_url:
            return {"service": service, "namespace": namespace, "spans": trace_summary(service)[:limit]}
        encoded = urllib.parse.urlencode({"q": service, "limit": int(limit)})
        url = f"{tempo_base_url.rstrip('/')}/api/search?{encoded}"
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = _safe_json_load(response.read().decode("utf-8"))
        spans = payload.get("traces", payload.get("spans", []))
        return {"service": service, "namespace": namespace, "spans": spans[:limit]}

    def get_dashboard_context(self, service: str, namespace: str) -> Dict[str, Any]:
        dashboard = dashboards(service)[0] if dashboards(service) else {}
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

    def execute_remediation(self, action: Dict[str, Any]) -> Dict[str, Any]:
        raise PermissionError("Live MCP tools are read-only. Use the isolated executor for approved remediations.")


def _probe_http_json(url: str, timeout_seconds: float) -> Dict[str, Any]:
    started = time.perf_counter()
    with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
        payload = _safe_json_load(response.read().decode("utf-8"))
    return {
        "status": "ready",
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
        "detail": "Endpoint responded successfully.",
        "payload_keys": sorted(list(payload.keys()))[:6],
    }


def _probe_http_text(url: str, timeout_seconds: float) -> Dict[str, Any]:
    started = time.perf_counter()
    with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8", errors="ignore")
    return {
        "status": "ready",
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
        "detail": body[:120] or "Endpoint responded successfully.",
    }


def probe_live_backend(active_probe: bool = False) -> Dict[str, Any]:
    settings = get_settings()
    components: Dict[str, Dict[str, Any]] = {
        "kubectl": {
            "configured": bool(settings.kubectl_binary),
            "status": "ready" if settings.kubeconfig_path else "not_configured",
            "detail": "Kubeconfig is available." if settings.kubeconfig_path else "KUBECONFIG is not configured.",
        },
        "prometheus": {
            "configured": bool(settings.prometheus_base_url),
            "status": "ready" if settings.prometheus_base_url else "not_configured",
            "detail": "Prometheus base URL is configured." if settings.prometheus_base_url else "PROMETHEUS_BASE_URL is not configured.",
        },
        "loki": {
            "configured": bool(settings.loki_base_url),
            "status": "ready" if settings.loki_base_url else "not_configured",
            "detail": "Loki base URL is configured." if settings.loki_base_url else "LOKI_BASE_URL is not configured.",
        },
        "tempo": {
            "configured": bool(os.getenv("TEMPO_BASE_URL", "").strip()),
            "status": "ready" if os.getenv("TEMPO_BASE_URL", "").strip() else "not_configured",
            "detail": "Tempo base URL is configured." if os.getenv("TEMPO_BASE_URL", "").strip() else "TEMPO_BASE_URL is not configured.",
        },
    }

    if not active_probe:
        return {
            "configured": settings.mcp_backend == "live",
            "active_probe": False,
            "components": components,
            "ready": all(item["status"] == "ready" for item in components.values() if item.get("configured")),
        }

    if settings.kubeconfig_path:
        try:
            adapter = KubectlLiveAdapter()
            started = time.perf_counter()
            raw = adapter._run("version", "--client", "-o", "json")
            parsed = json.loads(raw)
            components["kubectl"] = {
                "configured": True,
                "status": "ready",
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "detail": f"kubectl client {parsed.get('clientVersion', {}).get('gitVersion', 'unknown')}",
            }
        except Exception as exc:
            components["kubectl"] = {"configured": True, "status": "degraded", "detail": str(exc)}

    if settings.prometheus_base_url:
        try:
            components["prometheus"] = {
                "configured": True,
                **_probe_http_json(f"{settings.prometheus_base_url.rstrip('/')}/api/v1/status/buildinfo", 3.0),
            }
        except Exception as exc:
            components["prometheus"] = {"configured": True, "status": "degraded", "detail": str(exc)}

    if settings.loki_base_url:
        try:
            components["loki"] = {
                "configured": True,
                **_probe_http_text(f"{settings.loki_base_url.rstrip('/')}/ready", 3.0),
            }
        except Exception as exc:
            components["loki"] = {"configured": True, "status": "degraded", "detail": str(exc)}

    tempo_base_url = os.getenv("TEMPO_BASE_URL", "").strip()
    if tempo_base_url:
        try:
            components["tempo"] = {
                "configured": True,
                **_probe_http_text(f"{tempo_base_url.rstrip('/')}/ready", 3.0),
            }
        except Exception as exc:
            components["tempo"] = {"configured": True, "status": "degraded", "detail": str(exc)}

    return {
        "configured": settings.mcp_backend == "live",
        "active_probe": True,
        "components": components,
        "ready": all(item["status"] == "ready" for item in components.values() if item.get("configured")),
    }
