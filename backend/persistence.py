"""Postgres persistence for incidents + audit trail (optional).

Disabled by default: with DATABASE_URL unset the app runs exactly as
before, everything in memory. With DATABASE_URL set, incidents and
audit entries are also written to Postgres and reloaded on startup,
so the story survives a restart. The high-frequency metric ring
buffer stays in memory on purpose — persisting every 5s sample would
buy nothing for the demo.

Fail-safe by design: every operation here is best-effort from the
caller's side (see store.py). If the database is down, the app keeps
running in memory and never crashes because of Postgres.

Driver: psycopg2-binary (requirements.txt). If psycopg (v3) is
installed instead, it is used as a fallback — both expose the same
connect/cursor/%s-placeholder surface this module uses.
"""
import json
import sys

# Incidents: one row per incident, upserted on every change so the
# latest status / investigation / timeline is always on disk.
# investigation, guardian, detection, timeline are JSON stored as TEXT
# (keeps this module driver-agnostic — no JSON adaptation needed).
_CREATE_INCIDENTS = """
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    service TEXT NOT NULL,
    metric TEXT NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    threshold DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    resolved_at TEXT,
    approved_by TEXT,
    resolution TEXT,
    investigation TEXT,
    guardian TEXT,
    detection TEXT,
    timeline TEXT
)
"""

# Audit: append-only. id is a plain serial so load order is stable.
_CREATE_AUDIT = """
CREATE TABLE IF NOT EXISTS audit (
    id SERIAL PRIMARY KEY,
    ts TEXT NOT NULL,
    incident_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL
)
"""

_UPSERT_INCIDENT = """
INSERT INTO incidents
    (id, service, metric, value, threshold, status, started_at,
     detected_at, resolved_at, approved_by, resolution,
     investigation, guardian, detection, timeline)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    service = EXCLUDED.service,
    metric = EXCLUDED.metric,
    value = EXCLUDED.value,
    threshold = EXCLUDED.threshold,
    status = EXCLUDED.status,
    started_at = EXCLUDED.started_at,
    detected_at = EXCLUDED.detected_at,
    resolved_at = EXCLUDED.resolved_at,
    approved_by = EXCLUDED.approved_by,
    resolution = EXCLUDED.resolution,
    investigation = EXCLUDED.investigation,
    guardian = EXCLUDED.guardian,
    detection = EXCLUDED.detection,
    timeline = EXCLUDED.timeline
"""

_SELECT_INCIDENTS = (
    "SELECT id, service, metric, value, threshold, status, started_at,"
    " detected_at, resolved_at, approved_by, resolution, investigation,"
    " guardian, detection, timeline FROM incidents ORDER BY started_at"
)
_SELECT_AUDIT = (
    "SELECT ts, incident_id, actor, action, detail FROM audit ORDER BY id"
)
_INSERT_AUDIT = (
    "INSERT INTO audit (ts, incident_id, actor, action, detail)"
    " VALUES (%s, %s, %s, %s, %s)"
)
_DELETE_INCIDENTS = "DELETE FROM incidents"

_INCIDENT_COLUMNS = (
    "id", "service", "metric", "value", "threshold", "status",
    "started_at", "detected_at", "resolved_at", "approved_by",
    "resolution", "investigation", "guardian", "detection", "timeline",
)
_JSON_COLUMNS = ("investigation", "guardian", "detection", "timeline")


def _get_driver():
    """psycopg2 first, psycopg (v3) as fallback, None if neither."""
    try:
        import psycopg2

        return psycopg2
    except ImportError:
        pass
    try:
        import psycopg

        return psycopg
    except ImportError:
        return None


class PostgresStore:
    """One connection, autocommit, for the lifetime of the process."""

    def __init__(self, database_url):
        driver = _get_driver()
        if driver is None:
            raise RuntimeError(
                "no Postgres driver installed (pip install psycopg2-binary)"
            )
        # Short timeout: a dead database must fail fast at startup, not
        # hang the demo.
        self._conn = driver.connect(database_url, connect_timeout=5)
        self._conn.autocommit = True
        with self._conn.cursor() as cur:
            cur.execute(_CREATE_INCIDENTS)
            cur.execute(_CREATE_AUDIT)

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def save_incident(self, incident):
        """Insert or update one incident row (full row, every time)."""
        values = []
        for col in _INCIDENT_COLUMNS:
            val = incident.get(col)
            if col in _JSON_COLUMNS:
                val = json.dumps(val) if val is not None else None
            values.append(val)
        with self._conn.cursor() as cur:
            cur.execute(_UPSERT_INCIDENT, values)

    def delete_all_incidents(self):
        with self._conn.cursor() as cur:
            cur.execute(_DELETE_INCIDENTS)

    def save_audit(self, entry):
        with self._conn.cursor() as cur:
            cur.execute(
                _INSERT_AUDIT,
                (
                    entry.get("ts", ""),
                    entry.get("incident_id", ""),
                    entry.get("actor", ""),
                    entry.get("action", ""),
                    entry.get("detail", ""),
                ),
            )

    def load_incidents(self):
        """All incidents, oldest first, as plain dicts (same shape as
        the in-memory incident dicts the detector builds)."""
        with self._conn.cursor() as cur:
            cur.execute(_SELECT_INCIDENTS)
            rows = cur.fetchall()
        incidents = []
        for row in rows:
            inc = dict(zip(_INCIDENT_COLUMNS, row))
            for col in _JSON_COLUMNS:
                raw = inc.get(col)
                if raw is None:
                    inc[col] = [] if col == "timeline" else None
                else:
                    try:
                        inc[col] = json.loads(raw)
                    except ValueError:
                        inc[col] = [] if col == "timeline" else None
            incidents.append(inc)
        return incidents

    def load_audit(self):
        with self._conn.cursor() as cur:
            cur.execute(_SELECT_AUDIT)
            rows = cur.fetchall()
        return [
            {
                "ts": ts,
                "incident_id": incident_id,
                "actor": actor,
                "action": action,
                "detail": detail,
            }
            for ts, incident_id, actor, action, detail in rows
        ]

    def load_all(self):
        return self.load_incidents(), self.load_audit()


def warn(message):
    print(f"[persistence] {message}", file=sys.stderr)
