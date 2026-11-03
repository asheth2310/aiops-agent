"""Persistence: in-memory default, DB fallback, round-trip, reset.

Most tests run against a fake Postgres driver (in-memory dicts behind
the same SQL surface persistence.py uses), so they need no server.
The real-database test at the bottom runs only when TEST_DATABASE_URL
is set to a throwaway database — it is skipped in CI.
"""
import os

import pytest

from backend import audit, detector, persistence, store


def _seed_degraded(service, n=12, latency_ms=180, error_rate=0.35):
    for _ in range(n):
        store.METRICS[service].append(
            {"ts": store.now_iso(), "latency_ms": latency_ms,
             "error_rate": error_rate, "status": "degraded"}
        )


class _FakeCursor:
    """Cursor over in-memory dicts; only the SQL persistence.py emits."""

    def __init__(self, db):
        self._db = db
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("CREATE TABLE"):
            return
        if s.startswith("INSERT INTO incidents"):
            row = dict(zip(persistence._INCIDENT_COLUMNS, params))
            self._db["incidents"][row["id"]] = row
        elif s.startswith("INSERT INTO audit"):
            self._db["audit"].append(tuple(params))
        elif s.startswith("SELECT") and "FROM incidents" in s:
            rows = sorted(
                self._db["incidents"].values(),
                key=lambda r: r["started_at"],
            )
            self._rows = [
                tuple(r[c] for c in persistence._INCIDENT_COLUMNS) for r in rows
            ]
        elif s.startswith("SELECT") and "FROM audit" in s:
            self._rows = list(self._db["audit"])
        elif s.startswith("DELETE FROM incidents"):
            self._db["incidents"].clear()
        else:
            raise AssertionError(f"fake driver: unhandled SQL: {s[:80]}")

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, db):
        self._db = db
        self.autocommit = True

    def cursor(self):
        return _FakeCursor(self._db)

    def close(self):
        pass


class _FakeDriver:
    def __init__(self, db):
        self._db = db

    def connect(self, url, connect_timeout=None):
        return _FakeConnection(self._db)


@pytest.fixture()
def fake_db(monkeypatch):
    """Persistence on, backed by the fake driver. DATABASE_URL set."""
    db = {"incidents": {}, "audit": []}
    monkeypatch.setattr(
        persistence, "_get_driver", lambda: _FakeDriver(db)
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/test")
    store.close_persistence()
    store.reset()
    store.INCIDENTS.clear()
    store.AUDIT.clear()
    yield db
    store.close_persistence()
    store.reset()
    store.INCIDENTS.clear()
    store.AUDIT.clear()
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture()
def clean(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store.close_persistence()
    store.reset()
    store.INCIDENTS.clear()
    store.AUDIT.clear()
    yield
    store.close_persistence()
    store.reset()
    store.INCIDENTS.clear()
    store.AUDIT.clear()


def test_in_memory_when_no_database_url(clean):
    assert store.init_persistence() is False
    assert store._PERSISTENT is False
    _seed_degraded("inventory")
    incident = detector.check("inventory")
    assert incident is not None
    assert len(store.INCIDENTS) == 1


def test_falls_back_when_db_unreachable(clean, monkeypatch):
    # Real driver, dead address: must fail fast and stay in-memory.
    monkeypatch.setenv("DATABASE_URL", "postgresql://127.0.0.1:1/nope")
    assert store.init_persistence() is False
    assert store._PERSISTENT is False
    _seed_degraded("inventory")
    assert detector.check("inventory") is not None


def test_incident_and_audit_survive_restart(fake_db):
    assert store.init_persistence() is True
    _seed_degraded("inventory")
    incident = detector.check("inventory")
    assert incident is not None
    assert incident["id"] in fake_db["incidents"]
    assert fake_db["incidents"][incident["id"]]["status"] == "detected"

    # A field change + an explicit save persists (the save point
    # main.py uses after the investigate / approve / reject batches).
    incident["status"] = "needs_approval"
    store.save_incident(incident)
    assert fake_db["incidents"][incident["id"]]["status"] == "needs_approval"

    audit.log(incident["id"], "investigator", "investigation_done", "cause=x")
    assert len(fake_db["audit"]) >= 1

    # Simulate a process restart: fresh in-memory state, same database.
    store.close_persistence()
    store.INCIDENTS.clear()
    store.AUDIT.clear()
    store._incident_counter = 0
    assert store.init_persistence() is True

    assert len(store.INCIDENTS) == 1
    loaded = store.INCIDENTS[0]
    assert loaded["id"] == incident["id"]
    assert loaded["service"] == "inventory"
    # It was still open at restart, so it loads back as superseded
    # (services start healthy) instead of blocking the detector.
    assert loaded["status"] == "superseded"
    assert "restart" in (loaded["resolution"] or "")
    # Counter resumed: the next incident is INC-002, not INC-001 again.
    assert store.next_incident_id() == "INC-002"
    # Audit trail reloaded, including the supersede entry.
    assert len(store.AUDIT) >= 1
    assert any(e["action"] == "incident_superseded" for e in store.AUDIT)


def test_reset_clears_incidents_keeps_audit(fake_db):
    assert store.init_persistence() is True
    _seed_degraded("inventory")
    assert detector.check("inventory") is not None
    assert len(fake_db["incidents"]) == 1

    store.reset()
    assert store.INCIDENTS == []
    assert fake_db["incidents"] == {}
    # Audit survives the reset, plus the demo_reset entry itself.
    assert len(fake_db["audit"]) >= 1
    assert any(row[3] == "demo_reset" for row in fake_db["audit"])


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to a throwaway Postgres to run",
)
def test_real_postgres_round_trip(clean):
    """Live check: needs a real server — see docs/persistence-rag.md.

    Run with a throwaway database, e.g.:
      TEST_DATABASE_URL=postgresql://user:pass@localhost:5433/aiops_test \
        python -m pytest backend/tests/test_persistence.py::test_real_postgres_round_trip -q
    """
    url = os.environ["TEST_DATABASE_URL"]
    # Wipe the shared throwaway DB first: earlier runs (incl. the
    # manual E2E) may have left incidents behind.
    persistence.PostgresStore(url).delete_all_incidents()
    assert store.init_persistence(url) is True
    try:
        _seed_degraded("inventory")
        incident = detector.check("inventory")
        assert incident is not None
        audit.log(incident["id"], "investigator", "investigation_done", "cause=x")

        store.close_persistence()
        store.INCIDENTS.clear()
        store.AUDIT.clear()
        store._incident_counter = 0
        assert store.init_persistence(url) is True
        assert len(store.INCIDENTS) == 1
        assert store.INCIDENTS[0]["id"] == incident["id"]
        assert len(store.AUDIT) >= 1
    finally:
        store.reset()
        store.close_persistence()
