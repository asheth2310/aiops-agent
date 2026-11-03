"""Shared state for the app.

Default: everything in memory, resets on restart (the original MVP).
Optional Postgres persistence: when DATABASE_URL is set (see
init_persistence), incidents and the audit trail are also written to
Postgres and reloaded on startup, through the same functions and
lists below — callers do not change. The high-frequency metric ring
buffer and log buffers stay in memory on purpose.

Fail-safe: if the database is unreachable at startup or dies mid-run,
the app logs a warning and keeps running in memory. Persistence must
never break the demo.
"""
import os
import sys
from collections import deque
from datetime import datetime, timezone

from . import persistence as _persistence_module


# --- Postgres persistence (optional, off unless DATABASE_URL is set) ---
_PG = None          # a persistence.PostgresStore, or None
_PERSISTENT = False  # True only while the DB connection is healthy

# Statuses that mean "still open". An incident left in one of these
# when the server restarts is superseded on load (see init_persistence):
# the services start healthy, so carrying an open incident over would
# block the detector for that service with nothing actually broken.
_OPEN_STATUSES = (
    "detected", "investigating", "needs_info",
    "needs_approval", "approved", "executing",
)


def _disable_persistence(reason):
    """Drop back to in-memory mode. Never raises."""
    global _PG, _PERSISTENT
    _PERSISTENT = False
    pg, _PG = _PG, None
    if pg is not None:
        try:
            pg.close()
        except Exception:
            pass
    _persistence_module.warn(f"{reason}; continuing in-memory.")


def _save_incident(incident):
    """Best-effort write-through for one incident. Never raises.

    Called on append, on add_timeline, and explicitly by main.py after
    any other mutation batch (see save_incident) — those three points
    cover every place the codebase changes an incident.
    """
    if not _PERSISTENT or _PG is None:
        return
    try:
        _PG.save_incident(incident)
    except Exception as exc:  # noqa: BLE001 - any DB failure disables persistence
        _disable_persistence(f"could not save incident ({exc})")


def save_incident(incident):
    """Public save point: persist an incident's current state now.

    Use after mutating an incident outside add_timeline (main.py does
    this after the investigate / approve / reject mutation batches).
    No-op when persistence is off. Never raises.
    """
    _save_incident(incident)


def _save_audit(entry):
    """Best-effort write-through for one audit entry. Never raises."""
    if not _PERSISTENT or _PG is None:
        return
    try:
        _PG.save_audit(entry)
    except Exception as exc:  # noqa: BLE001 - any DB failure disables persistence
        _disable_persistence(f"could not save audit entry ({exc})")


class _IncidentList(list):
    """The incident list, with a Postgres write-through.

    append() upserts the incident row; clear() also deletes the
    incident rows (used by demo reset). In-memory mode these hooks
    are no-ops — plain list behaviour, byte for byte.
    """

    def append(self, incident):
        super().append(incident)
        _save_incident(incident)

    def extend(self, incidents):
        for incident in incidents:
            self.append(incident)

    def clear(self):
        super().clear()
        if _PERSISTENT and _PG is not None:
            try:
                _PG.delete_all_incidents()
            except Exception as exc:  # noqa: BLE001
                _disable_persistence(f"could not clear incidents ({exc})")


def init_persistence(database_url=None):
    """Turn on Postgres persistence if DATABASE_URL is set.

    Connects, creates tables if missing, loads saved incidents + audit
    into the in-memory lists. Returns True if persistence is on,
    False if in-memory (unset URL, no driver, or any DB error).
    Safe to call more than once; a second call is a no-op while on.
    """
    global _PG, _PERSISTENT, _incident_counter
    if _PERSISTENT:
        return True
    url = (database_url or os.environ.get("DATABASE_URL", "")).strip()
    if not url:
        return False
    try:
        pg = _persistence_module.PostgresStore(url)
        incidents, audit_entries = pg.load_all()
    except Exception as exc:  # noqa: BLE001 - any DB failure means in-memory
        _persistence_module.warn(
            f"Postgres unavailable ({exc}); running in-memory."
        )
        return False

    # Populate the in-memory lists while still in in-memory mode, so
    # the write-through hooks stay quiet during the load.
    INCIDENTS.clear()
    AUDIT.clear()
    for incident in incidents:
        INCIDENTS.append(incident)
    for entry in audit_entries:
        AUDIT.append(entry)
    _incident_counter = 0
    for incident in incidents:
        try:
            num = int(str(incident.get("id", "")).split("-")[-1])
            _incident_counter = max(_incident_counter, num)
        except ValueError:
            continue

    _PG = pg
    _PERSISTENT = True
    # From here on, mutations write through (append / add_timeline /
    # explicit save_incident calls in main.py).
    # Open incidents cannot survive a restart honestly: the services
    # start healthy, so an open incident would block the detector for
    # that service with nothing broken. Keep it as history, marked.
    for incident in list(INCIDENTS):
        if incident.get("status") in _OPEN_STATUSES:
            incident["status"] = "superseded"
            incident["resolution"] = (
                "Carried over from before a restart; the service "
                "started healthy."
            )
            add_timeline(
                incident, "superseded",
                "server restarted — incident kept as history",
            )
            add_audit(
                incident["id"], "system", "incident_superseded",
                "open at restart; service started healthy",
            )
    return True


def close_persistence():
    """Turn persistence off (tests / shutdown). In-memory state kept.

    Quiet by design — unlike _disable_persistence, closing on purpose
    is not a warning.
    """
    global _PG, _PERSISTENT
    _PERSISTENT = False
    pg, _PG = _PG, None
    if pg is not None:
        try:
            pg.close()
        except Exception:
            pass


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

INCIDENTS = _IncidentList()  # list of dicts, newest last
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
    _save_audit(entry)
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
    _save_incident(incident)
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
