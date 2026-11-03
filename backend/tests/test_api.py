"""API: health, metrics, logs, eval, reset — via TestClient."""
import pytest
from fastapi.testclient import TestClient

from backend import detector, store
from backend.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        store.reset()
        store.INCIDENTS.clear()
        yield c
        store.reset()
        store.INCIDENTS.clear()


def test_health_has_three_services(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()["services"]]
    assert names == ["orders", "inventory", "payments"]
    for svc in resp.json()["services"]:
        assert "total_requests" in svc
        assert "requests_per_min" in svc
        assert "uptime_pct" in svc


def test_health_reports_investigator_live(client, monkeypatch):
    """The header badge reads this: live only when GEMINI_LIVE=1 and
    a live path exists (API key env var or the helper binary)."""
    import os

    from backend import investigator

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_LIVE", "0")
    monkeypatch.setattr(investigator, "HELPER", "/nonexistent/helper.py")
    assert client.get("/health").json()["investigator_live"] is False

    monkeypatch.setenv("GEMINI_LIVE", "1")
    assert client.get("/health").json()["investigator_live"] is False

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-a-real-credential")
    assert client.get("/health").json()["investigator_live"] is True

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(investigator, "HELPER", os.path.abspath(__file__))
    assert client.get("/health").json()["investigator_live"] is True


def test_metrics_404_for_unknown_service(client):
    resp = client.get("/metrics?service=nope")
    assert resp.status_code == 404


def test_metrics_returns_samples(client):
    resp = client.get("/metrics?service=inventory&limit=3")
    assert resp.status_code == 200
    assert resp.json()["service"] == "inventory"
    assert isinstance(resp.json()["samples"], list)


def test_logs_returns_lines(client):
    # Seed a line: the fixture reset clears the startup tick's logs and
    # the next sampler tick is 5s away, so the endpoint is tested with
    # a known line rather than a timing race.
    store.LOGS["inventory"].append(
        "2026-11-02T00:00:00 [INFO] inventory: request ok in 90ms"
    )
    resp = client.get("/logs?limit=5")
    assert resp.status_code == 200
    logs = resp.json()["logs"]
    assert len(logs) >= 1
    assert {"ts", "service", "level", "message"} <= set(logs[0])


def test_eval_endpoint_without_results(client):
    # Results file may exist from a local eval run; either shape is valid.
    resp = client.get("/eval")
    assert resp.status_code == 200
    body = resp.json()
    assert "runs" in body and "summary" in body


def test_demo_reset_clears_incidents(client):
    for _ in range(12):
        store.METRICS["inventory"].append(
            {"ts": store.now_iso(), "latency_ms": 180,
             "error_rate": 0.35, "status": "degraded"}
        )
    assert detector.check("inventory") is not None
    assert len(client.get("/incidents").json()["incidents"]) == 1
    resp = client.post("/demo/reset", json={})
    assert resp.status_code == 200
    assert client.get("/incidents").json()["incidents"] == []


def test_break_rejects_unknown_service(client):
    resp = client.post("/demo/break", json={"service": "nope", "type": "latency"})
    assert resp.status_code == 400


def test_root_serves_dashboard_or_json(client):
    resp = client.get("/")
    assert resp.status_code == 200
