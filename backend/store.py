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
    },
    "inventory": {
        "name": "inventory",
        "status": "healthy",
        "latency_ms": 78,
        "error_rate": 0.01,
        "broken": False,
        "break_type": None,
        "broken_since": None,
    },
}

# Last 200 samples per service: {ts, latency_ms, error_rate, status}
METRICS = {name: deque(maxlen=200) for name in SERVICES}
# Last 50 log lines per service, plain strings with timestamps.
LOGS = {name: deque(maxlen=50) for name in SERVICES}

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
