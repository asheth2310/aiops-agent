"""Shared in-memory state for the MVP.

No database in the MVP on purpose: metrics, logs, incidents and the
audit trail all live in memory and reset when the server restarts.
That is a documented MVP cut, not a design goal.
"""
from collections import deque
from datetime import datetime, timezone

SERVICES = {
    "orders": {
        "name": "orders",
        "status": "healthy",
        "latency_ms": 82,
        "error_rate": 0.01,
        "broken": False,
        "break_type": None,
        "broken_since": None,
        "total_requests": 0,
        "ticks": 0,
        "healthy_ticks": 0,
    },
    "inventory": {
        "name": "inventory",
        "status": "healthy",
        "latency_ms": 78,
        "error_rate": 0.01,
        "broken": False,
        "break_type": None,
        "broken_since": None,
        "total_requests": 0,
        "ticks": 0,
        "healthy_ticks": 0,
    },
    "payments": {
        "name": "payments",
        "status": "healthy",
        "latency_ms": 105,
        "error_rate": 0.01,
        "broken": False,
        "break_type": None,
        "broken_since": None,
        "total_requests": 0,
        "ticks": 0,
        "healthy_ticks": 0,
    },
}

# Last 200 samples per service: {ts, latency_ms, error_rate, status}
METRICS = {name: deque(maxlen=200) for name in SERVICES}
# Last 50 log lines per service, plain strings with timestamps.
LOGS = {name: deque(maxlen=50) for name in SERVICES}
# Per-service (ts, total_requests) snapshots, one per simulator tick.
# 13 snapshots cover 60s at the 5s tick, so req/min is the counter
# delta over the last minute.
REQUEST_SNAPSHOTS = {name: deque(maxlen=13) for name in SERVICES}

INCIDENTS = []  # list of dicts, newest last
AUDIT = []  # list of dicts, newest last
_incident_counter = 0


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def next_incident_id():
    global _incident_counter
    _incident_counter += 1
    return f"INC-{_incident_counter:03d}"


def add_audit(incident_id, actor, action, detail=""):
    entry = {
        "ts": now_iso(),
        "incident_id": incident_id,
        "actor": actor,
        "action": action,
        "detail": detail,
    }
    AUDIT.append(entry)
    return entry


def add_timeline(incident, stage, detail=""):
    """Append a stage to an incident's timeline (detected → … → resolved).

    Each entry carries its own timestamp, so the dashboard can show how
    long every stage lasted. Kept on the incident dict so it serializes
    with /incidents automatically.
    """
    incident.setdefault("timeline", []).append(
        {"stage": stage, "ts": now_iso(), "detail": detail}
    )
    return incident["timeline"]


def requests_per_min(service_name):
    """Requests in the last 60s, from the counter snapshots.

    Latest total minus the total at the oldest snapshot still within
    the last 60 seconds (snapshots arrive every 5s, so the deque holds
    ~13 of them). Falls back to the oldest snapshot if the window is
    shorter than a minute, e.g. right after startup.
    """
    from datetime import datetime

    snaps = REQUEST_SNAPSHOTS[service_name]
    if not snaps:
        return 0
    now = datetime.now(timezone.utc)
    base = snaps[0][1]
    for ts_iso, total in snaps:
        try:
            ts = datetime.fromisoformat(ts_iso)
        except ValueError:
            continue
        if (now - ts).total_seconds() <= 60:
            base = total
            break
    return max(0, SERVICES[service_name]["total_requests"] - base)


def open_incident(service_name):
    """Return the still-open incident for a service, or None."""
    for inc in reversed(INCIDENTS):
        if inc["service"] == service_name and inc["status"] in (
            "detected",
            "investigating",
            "needs_info",
            "needs_approval",
            "approved",
            "executing",
        ):
            return inc
    return None


def reset():
    """Demo reset: recover all services, clear incidents, metrics and
    logs. The audit trail is kept, and a 'demo reset' entry is appended
    so the reset itself is visible in the trail."""
    for name, svc in SERVICES.items():
        svc["broken"] = False
        svc["break_type"] = None
        svc["broken_since"] = None
        svc["status"] = "healthy"
        svc["error_rate"] = 0.01
        svc["total_requests"] = 0
        svc["ticks"] = 0
        svc["healthy_ticks"] = 0
        METRICS[name].clear()
        LOGS[name].clear()
        REQUEST_SNAPSHOTS[name].clear()
    # Healthy starting latencies per service (match the simulator ranges).
    SERVICES["orders"]["latency_ms"] = 82
    SERVICES["inventory"]["latency_ms"] = 78
    SERVICES["payments"]["latency_ms"] = 105
    INCIDENTS.clear()
    add_audit("—", "demo", "demo_reset",
              "all services recovered; incidents, metrics and logs cleared")
