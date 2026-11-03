"""Detector: hard thresholds only, no LLM involved."""
import pytest

from backend import detector, store


@pytest.fixture(autouse=True)
def clean():
    store.reset()
    store.INCIDENTS.clear()
    yield
    store.reset()
    store.INCIDENTS.clear()


def _seed(service, n, latency_ms, error_rate, status="degraded"):
    for _ in range(n):
        store.METRICS[service].append(
            {"ts": store.now_iso(), "latency_ms": latency_ms,
             "error_rate": error_rate, "status": status}
        )


def test_error_rule_fires():
    _seed("inventory", 12, latency_ms=180, error_rate=0.35)
    incident = detector.check("inventory")
    assert incident is not None
    assert incident["metric"] == "error_rate"
    assert incident["value"] == pytest.approx(0.35)
    assert incident["threshold"] == 0.20


def test_error_rule_does_not_fire_when_healthy():
    _seed("inventory", 12, latency_ms=90, error_rate=0.02, status="healthy")
    assert detector.check("inventory") is None


def test_latency_rule_needs_two_windows():
    # One minute of slow samples is not enough for the latency rule...
    _seed("inventory", 12, latency_ms=850, error_rate=0.01)
    assert detector.check("inventory") is None
    # ...but two consecutive slow minutes fire it.
    _seed("inventory", 12, latency_ms=850, error_rate=0.01)
    incident = detector.check("inventory")
    assert incident is not None
    assert incident["metric"] == "latency_p95_ms"
    assert incident["value"] >= 500


def test_no_refire_after_recovery():
    _seed("inventory", 12, latency_ms=180, error_rate=0.35)
    incident = detector.check("inventory")
    assert incident is not None
    # Service recovers: a fresh healthy sample is the latest one.
    # The stale degraded window must not refire a phantom incident.
    store.METRICS["inventory"].append(
        {"ts": store.now_iso(), "latency_ms": 90,
         "error_rate": 0.01, "status": "healthy"}
    )
    assert detector.check("inventory") is None


def test_no_second_incident_while_one_is_open():
    _seed("inventory", 12, latency_ms=180, error_rate=0.35)
    assert detector.check("inventory") is not None
    assert detector.check("inventory") is None
