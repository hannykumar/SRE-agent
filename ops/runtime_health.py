from __future__ import annotations

from typing import Any, Dict

from agent.model_runtime import active_model_status, passive_model_status
from mcp_tools.live_backends import probe_live_backend
from ops.settings import get_settings


def runtime_health_snapshot(active_probe: bool = False) -> Dict[str, Any]:
    settings = get_settings()
    model = active_model_status(force=active_probe) if active_probe else passive_model_status()
    live_backends = probe_live_backend(active_probe=active_probe)
    return {
        "active_probe": active_probe,
        "planner": model,
        "live_backends": live_backends,
        "mcp_backend": settings.mcp_backend,
        "planner_provider": settings.planner_provider,
        "planner_model": settings.planner_model,
    }
