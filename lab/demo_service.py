from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest


app = FastAPI(title="sre-alert-lab-demo-service")

REQUESTS = Counter(
    "demo_http_requests_total",
    "HTTP requests handled by the demo alert lab service.",
    ["path", "method", "status"],
)
FAILURE_MODE = Gauge(
    "demo_failure_mode",
    "Whether the demo service is currently forced into 503 mode.",
)
INCIDENT_SIGNAL = Gauge(
    "demo_incident_signal",
    "Synthetic alert signal for the incident lab scenarios.",
    ["scenario", "incident_id"],
)
MODE_INFO = Gauge(
    "demo_mode_info",
    "Mode indicator for the demo alert lab service.",
    ["mode"],
)

Mode = Literal["ok", "error503", "crashloop", "dnsfailure"]
STATE: dict[str, Mode] = {"mode": "ok"}


def _record(path: str, method: str, status: int) -> None:
    REQUESTS.labels(path=path, method=method, status=str(status)).inc()


def _set_mode(mode: Mode) -> dict[str, str]:
    STATE["mode"] = mode
    FAILURE_MODE.set(1 if mode == "error503" else 0)
    for known_mode in ("ok", "error503", "crashloop", "dnsfailure"):
        MODE_INFO.labels(mode=known_mode).set(1 if mode == known_mode else 0)

    INCIDENT_SIGNAL.labels(scenario="service503", incident_id="INC-002").set(1 if mode == "error503" else 0)
    INCIDENT_SIGNAL.labels(scenario="crashloop", incident_id="INC-001").set(1 if mode == "crashloop" else 0)
    INCIDENT_SIGNAL.labels(scenario="dnsfailure", incident_id="INC-003").set(1 if mode == "dnsfailure" else 0)
    return {"mode": mode}


@app.get("/")
def root() -> dict[str, str]:
    if STATE["mode"] == "error503":
        _record("/", "GET", 503)
        raise HTTPException(status_code=503, detail="demo upstream timeout")
    _record("/", "GET", 200)
    return {"ok": "true", "mode": STATE["mode"]}


@app.get("/status")
def status() -> dict[str, Any]:
    _record("/status", "GET", 200)
    mode = STATE["mode"]
    return {
        "mode": mode,
        "signals": {
            "INC-001": mode == "crashloop",
            "INC-002": mode == "error503",
            "INC-003": mode == "dnsfailure",
        },
    }


@app.post("/admin/mode/{mode}")
def set_mode(mode: Mode) -> dict[str, str]:
    _record(f"/admin/mode/{mode}", "POST", 200)
    return _set_mode(mode)


@app.post("/admin/reset")
def reset_mode() -> dict[str, str]:
    _record("/admin/reset", "POST", 200)
    return _set_mode("ok")


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


_set_mode("ok")
