from __future__ import annotations

import copy
from typing import Any, Dict, List

from mcp_tools.mock_mcp import MockMCP


def _base_incident(incident_id: str) -> Dict[str, Any]:
    return copy.deepcopy(MockMCP(incident_id).data)


def _clone(base: Dict[str, Any], replay_id: str, title: str, description: str, expected: str, should_confirm: bool = True) -> Dict[str, Any]:
    payload = copy.deepcopy(base)
    payload["incident_id"] = replay_id
    payload["title"] = title
    payload["description"] = description
    payload["expected_incident_type"] = expected
    payload["should_confirm"] = should_confirm
    return payload


def generate_replayed_incidents() -> List[Dict[str, Any]]:
    replays: List[Dict[str, Any]] = []

    oom = _base_incident("INC-001")
    for idx in range(1, 7):
        replay = _clone(
            oom,
            f"REPLAY-CL-{idx:03d}",
            f"Checkout crash loop replay {idx}",
            f"Checkout pods are restarting after the latest traffic burst. Describe shows OOMKilled and restart count is high. variation={idx}",
            "CrashLoopBackOff",
        )
        replay["metrics"]["memory_percent"] = min(98, 90 + idx)
        replay["pod_status"]["restarts"] = 8 + idx
        replay["logs_tail"] = oom["logs_tail"] + [f"ERROR out of memory replay {idx}"]
        replay["severity"] = "sev1"
        replays.append(replay)

    svc = _base_incident("INC-002")
    for idx in range(1, 7):
        replay = _clone(
            svc,
            f"REPLAY-503-{idx:03d}",
            f"API 503 replay {idx}",
            f"Users see intermittent 503 responses and upstream timeout to payments. replay={idx}",
            "Service503",
        )
        replay["service"] = "api"
        replay["metrics"]["error_rate_percent"] = 8 + idx
        replay["metrics"]["p95_latency_ms"] = 1800 + (idx * 120)
        replay["logs_tail"] = svc["logs_tail"] + [f"ERROR returning 503 due to upstream timeout replay={idx}"]
        replay["severity"] = "sev1"
        replays.append(replay)

    dns = _base_incident("INC-003")
    for idx in range(1, 7):
        replay = _clone(
            dns,
            f"REPLAY-DNS-{idx:03d}",
            f"DNS failure replay {idx}",
            f"Workers cannot resolve internal service names. NXDOMAIN and could not resolve host are increasing. replay={idx}",
            "DNSFailure",
        )
        replay["service"] = "worker"
        replay["metrics"]["dns_error_rate_percent"] = 10 + idx
        replay["logs_tail"] = dns["logs_tail"] + [f"ERROR could not resolve host replay={idx}"]
        replay["severity"] = "sev2"
        replays.append(replay)

    for idx in range(1, 6):
        replay = _clone(
            svc,
            f"REPLAY-CPU-{idx:03d}",
            f"High CPU replay {idx}",
            f"API pods are saturated on CPU and customer latency is rising. replay={idx}",
            "HighCPU",
        )
        replay["service"] = "api"
        replay["metrics"]["cpu_percent"] = 88 + idx
        replay["metrics"]["p95_latency_ms"] = 1200 + (idx * 90)
        replay["metrics"]["error_rate_percent"] = 2 + idx
        replay["logs_tail"] = ["WARN hot path consuming CPU", f"WARN throttling replay={idx}"]
        replay["severity"] = "sev2"
        replays.append(replay)

    for idx in range(1, 6):
        replay = _clone(
            oom,
            f"REPLAY-MEM-{idx:03d}",
            f"High memory replay {idx}",
            f"Checkout memory rises steadily over the last hour and users report slow responses. replay={idx}",
            "HighMemory",
        )
        replay["metrics"]["memory_percent"] = 86 + idx
        replay["pod_status"]["restarts"] = 1
        replay["pod_status"]["reason"] = "Running"
        replay["pod_describe"]["last_state"]["terminated"]["reason"] = "Completed"
        replay["logs_tail"] = ["WARN heap growth detected", f"WARN memory pressure replay={idx}"]
        replay["severity"] = "sev2"
        replays.append(replay)

    for idx in range(1, 6):
        replay = _clone(
            svc,
            f"REPLAY-DEPLOY-{idx:03d}",
            f"Deployment regression replay {idx}",
            f"API errors started shortly after the latest release. replay={idx}",
            "DeploymentRegression",
        )
        replay["service"] = "api"
        replay["metrics"]["error_rate_percent"] = 7 + idx
        replay["logs_tail"] = ["ERROR feature flag path failing after deploy", f"ERROR rollout regression replay={idx}"]
        replay["severity"] = "sev1"
        replays.append(replay)

    for idx in range(1, 6):
        replay = _clone(
            svc,
            f"REPLAY-DB-{idx:03d}",
            f"Database connection replay {idx}",
            f"API requests are failing on database calls with pool timeout and connection refused. replay={idx}",
            "DatabaseConnectionFailure",
        )
        replay["service"] = "api"
        replay["logs_tail"] = [
            "ERROR timeout acquiring db connection",
            f"ERROR connection refused to payments-db replay={idx}",
        ]
        replay["metrics"]["error_rate_percent"] = 6 + idx
        replay["severity"] = "sev1"
        replays.append(replay)

    for idx in range(1, 5):
        replay = _clone(
            svc,
            f"REPLAY-TLS-{idx:03d}",
            f"TLS certificate replay {idx}",
            f"Ingress requests fail during TLS handshakes and clients report x509 errors. replay={idx}",
            "TLSCertificateExpiry",
        )
        replay["service"] = "ingress"
        replay["logs_tail"] = ["ERROR tls handshake failed", f"ERROR x509 certificate expired replay={idx}"]
        replay["metrics"]["error_rate_percent"] = 9 + idx
        replay["severity"] = "sev1"
        replays.append(replay)

    for idx in range(1, 6):
        replay = _clone(
            dns,
            f"REPLAY-QUEUE-{idx:03d}",
            f"Queue backlog replay {idx}",
            f"Worker queue depth is growing and lag is increasing. replay={idx}",
            "QueueBacklog",
        )
        replay["service"] = "worker"
        replay["logs_tail"] = ["WARN queue depth rising", f"ERROR retrying job due to backlog replay={idx}"]
        replay["metrics"]["p95_latency_ms"] = 1300 + (idx * 100)
        replay["metrics"]["error_rate_percent"] = 3 + idx
        replay["severity"] = "sev2"
        replays.append(replay)

    for idx in range(1, 6):
        replay = _clone(
            svc,
            f"REPLAY-LAT-{idx:03d}",
            f"Dependency latency replay {idx}",
            f"API dependency latency is spiking before hard errors appear. replay={idx}",
            "DependencyLatency",
        )
        replay["service"] = "api"
        replay["metrics"]["p95_latency_ms"] = 1500 + (idx * 110)
        replay["metrics"]["error_rate_percent"] = 2 + idx
        replay["logs_tail"] = ["WARN upstream timeout retrying dependency", f"WARN dependency latency replay={idx}"]
        replay["severity"] = "sev2"
        replays.append(replay)

    for idx in range(1, 5):
        replay = _clone(
            svc,
            f"REPLAY-NODE-{idx:03d}",
            f"Node not ready replay {idx}",
            f"Multiple pods degraded after node scheduling instability. replay={idx}",
            "NodeNotReady",
        )
        replay["service"] = "checkout"
        replay["cluster_events"] = [
            "Warning NodeNotReady Node worker-17 not ready",
            f"Warning Evicted Pod rescheduled replay={idx}",
        ]
        replay["metrics"]["error_rate_percent"] = 4 + idx
        replay["severity"] = "sev1"
        replays.append(replay)

    for idx in range(1, 5):
        replay = _clone(
            oom,
            f"REPLAY-DISK-{idx:03d}",
            f"Disk pressure replay {idx}",
            f"Pods are showing storage write failures and node pressure alerts. replay={idx}",
            "DiskPressure",
        )
        replay["cluster_events"] = [
            "Warning DiskPressure Node worker-22 has disk pressure",
            f"Warning Evicted Pod evicted due to ephemeral storage replay={idx}",
        ]
        replay["logs_tail"] = ["ERROR no space left on device", f"ERROR disk pressure replay={idx}"]
        replay["severity"] = "sev2"
        replays.append(replay)

    unknown = _base_incident("INC-004")
    for idx in range(1, 6):
        replay = _clone(
            unknown,
            f"REPLAY-UNK-{idx:03d}",
            f"Ambiguous replay {idx}",
            f"Checkout feels slow but the signals are weak and inconsistent. replay={idx}",
            "Unknown",
            should_confirm=False,
        )
        replay["metrics"]["p95_latency_ms"] = 380 + (idx * 12)
        replay["metrics"]["error_rate_percent"] = 1 + (idx % 2)
        replay["logs_tail"] = unknown["logs_tail"] + [f"WARN retry observed replay={idx}"]
        replay["severity"] = "sev3"
        replays.append(replay)

    return replays
