from __future__ import annotations

from typing import Any, Dict, List


def supported_alert_sources() -> List[Dict[str, Any]]:
    return [
        {
            "source_type": "grafana",
            "name": "Grafana",
            "ingest_path": "/alerts/grafana/webhook",
            "description": "Grafana-managed alert webhook adapter for the observability lab and external Grafana instances.",
            "demo_ready": True,
            "status_handling": ["firing", "resolved"],
        },
        {
            "source_type": "generic",
            "name": "Generic Webhook",
            "ingest_path": "/alerts/events",
            "description": "Platform-agnostic alert webhook adapter for external monitoring systems and future Nagios-style events.",
            "demo_ready": True,
            "status_handling": ["firing", "resolved"],
        },
    ]


def _select_alert_source(payload: Dict[str, Any]) -> Dict[str, Any]:
    alerts = payload.get("alerts") or []
    firing = [alert for alert in alerts if str(alert.get("status", "firing")).lower() == "firing"]
    if firing:
        return firing[0]
    if alerts:
        return alerts[0]
    return payload


def _infer_incident_id(alertname: str, summary: str, labels: Dict[str, Any]) -> tuple[str, str]:
    combined_text = " ".join(
        [
            alertname,
            summary,
            str(labels.get("service", "")),
            str(labels.get("severity", "")),
            str(labels.get("scenario", "")),
        ]
    ).lower()
    label_incident_id = str(labels.get("incident_id") or "").strip().upper()
    label_scenario = str(labels.get("scenario") or "").strip().lower()

    if label_incident_id in {"INC-001", "INC-002", "INC-003"}:
        return label_incident_id, label_scenario or f"alert_{label_incident_id.lower()}"
    if any(token in combined_text for token in ("5xx", "503", "upstream", "demo-api")):
        return "INC-002", "demo_service_high_5xx"
    if any(token in combined_text for token in ("crashloop", "oom", "oomkilled")):
        return "INC-001", "demo_service_crashloop"
    if any(token in combined_text for token in ("dns", "nxdomain", "resolve")):
        return "INC-003", "demo_service_dns_failure"
    return "INC-004", "unknown_alert"


def normalize_grafana_alert(payload: Dict[str, Any]) -> Dict[str, Any]:
    selected = _select_alert_source(payload)
    labels = dict(payload.get("commonLabels") or {})
    labels.update(selected.get("labels") or {})
    annotations = dict(payload.get("commonAnnotations") or {})
    annotations.update(selected.get("annotations") or {})

    alertname = str(labels.get("alertname") or payload.get("title") or payload.get("ruleName") or "grafana-alert").strip()
    summary = str(
        annotations.get("summary")
        or annotations.get("description")
        or payload.get("message")
        or payload.get("title")
        or alertname
    ).strip()
    incident_id, scenario = _infer_incident_id(alertname, summary, labels)
    return {
        "source_type": "grafana",
        "incident_id": incident_id,
        "scenario": scenario,
        "alertname": alertname,
        "summary": summary,
        "status": str(payload.get("status") or selected.get("status") or "firing").lower(),
        "fingerprint": str(selected.get("fingerprint") or ""),
        "starts_at": str(selected.get("startsAt") or ""),
        "labels": labels,
        "annotations": annotations,
        "source": selected,
    }


def normalize_generic_alert(payload: Dict[str, Any]) -> Dict[str, Any]:
    selected = dict(payload.get("alert") or payload)
    labels = dict(selected.get("labels") or {})
    annotations = dict(selected.get("annotations") or {})

    alertname = str(selected.get("alertname") or payload.get("title") or labels.get("alertname") or "generic-alert").strip()
    summary = str(
        selected.get("summary")
        or annotations.get("summary")
        or payload.get("summary")
        or payload.get("message")
        or alertname
    ).strip()
    if payload.get("service") and "service" not in labels:
        labels["service"] = payload.get("service")
    if payload.get("severity") and "severity" not in labels:
        labels["severity"] = payload.get("severity")

    incident_id, scenario = _infer_incident_id(alertname, summary, labels)
    return {
        "source_type": "generic",
        "incident_id": str(payload.get("incident_id") or incident_id).strip().upper() or "INC-004",
        "scenario": str(payload.get("scenario") or scenario).strip().lower() or "generic_alert",
        "alertname": alertname,
        "summary": summary,
        "status": str(payload.get("status") or selected.get("status") or "firing").lower(),
        "fingerprint": str(payload.get("fingerprint") or selected.get("fingerprint") or ""),
        "starts_at": str(payload.get("starts_at") or selected.get("starts_at") or ""),
        "labels": labels,
        "annotations": annotations,
        "source": selected,
    }


def normalize_alert_event(source_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized_type = str(source_type or "generic").strip().lower()
    if normalized_type == "grafana":
        return normalize_grafana_alert(payload)
    if normalized_type == "generic":
        return normalize_generic_alert(payload)
    raise ValueError(f"Unsupported alert source: {source_type}")
