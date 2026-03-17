"""
True MCP server for live SRE read tools.

The tool names match the mock MCP server so the agent can switch backends
without changing the LangGraph workflow.
"""

from __future__ import annotations

from typing import Any, Dict

from mcp.server.fastmcp import FastMCP

from mcp_tools.live_backends import LiveSREBackend


mcp = FastMCP("live-sre-tools")
backend = LiveSREBackend()


@mcp.tool()
def get_pod_status(incident_id: str, service: str, namespace: str) -> Dict[str, Any]:
    return backend.get_pod_status(service=service, namespace=namespace)


@mcp.tool()
def describe_pod(incident_id: str, pod_name: str, namespace: str) -> Dict[str, Any]:
    return backend.describe_pod(pod_name=pod_name, namespace=namespace)


@mcp.tool()
def get_metrics(incident_id: str, service: str, namespace: str) -> Dict[str, Any]:
    return backend.get_metrics(service=service, namespace=namespace)


@mcp.tool()
def get_pod_logs(incident_id: str, pod_name: str, namespace: str, lines: int = 200) -> list[str]:
    return backend.get_pod_logs(pod_name=pod_name, namespace=namespace, lines=lines)


@mcp.tool()
def get_cluster_events(incident_id: str, namespace: str) -> list[str]:
    return backend.get_cluster_events(namespace=namespace)


@mcp.tool()
def query_prometheus(incident_id: str, expression: str) -> Dict[str, Any]:
    return backend.query_prometheus(expression=expression)


@mcp.tool()
def query_loki(incident_id: str, service: str, namespace: str, limit: int = 50) -> Dict[str, Any]:
    return backend.query_loki(service=service, namespace=namespace, limit=limit)


@mcp.tool()
def query_tempo(incident_id: str, service: str, namespace: str, limit: int = 20) -> Dict[str, Any]:
    return backend.query_tempo(service=service, namespace=namespace, limit=limit)


@mcp.tool()
def get_dashboard_context(incident_id: str, service: str, namespace: str) -> Dict[str, Any]:
    return backend.get_dashboard_context(service=service, namespace=namespace)


@mcp.tool()
def get_recent_deploys(incident_id: str, service: str, namespace: str) -> list[Dict[str, Any]]:
    return backend.get_recent_deploys(service=service, namespace=namespace)


@mcp.tool()
def get_service_owner(incident_id: str, service: str) -> Dict[str, Any]:
    return backend.get_service_owner(service=service)


@mcp.tool()
def get_incident_history(incident_id: str, service: str, limit: int = 10) -> list[Dict[str, Any]]:
    return backend.get_incident_history(service=service, limit=limit)


@mcp.tool()
def execute_remediation(incident_id: str, action: Dict[str, Any]) -> Dict[str, Any]:
    return backend.execute_remediation(action=action)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
