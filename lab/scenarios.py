from __future__ import annotations

from typing import Any, Dict


ALERT_LAB_SCENARIOS: Dict[str, Dict[str, Any]] = {
    "INC-001": {
        "mode": "crashloop",
        "title": "CrashLoop / OOM",
        "alertname": "Demo API CrashLoop OOM Signal",
        "query": 'demo_incident_signal{scenario="crashloop",incident_id="INC-001"}',
        "resolved_below": 0.5,
    },
    "INC-002": {
        "mode": "error503",
        "title": "High 5xx / 503",
        "alertname": "Demo API High 5xx Rate",
        "query": (
            'sum(rate(demo_http_requests_total{path="/",status=~"5.."}[1m])) '
            '/ clamp_min(sum(rate(demo_http_requests_total{path="/"}[1m])), 0.001)'
        ),
        "resolved_below": 0.2,
    },
    "INC-003": {
        "mode": "dnsfailure",
        "title": "DNS Failure",
        "alertname": "Demo API DNS Failure Signal",
        "query": 'demo_incident_signal{scenario="dnsfailure",incident_id="INC-003"}',
        "resolved_below": 0.5,
    },
}


def get_alert_lab_scenario(incident_id: str) -> Dict[str, Any] | None:
    return ALERT_LAB_SCENARIOS.get(str(incident_id).strip().upper())


def is_alert_lab_incident(incident_id: str) -> bool:
    return get_alert_lab_scenario(incident_id) is not None
