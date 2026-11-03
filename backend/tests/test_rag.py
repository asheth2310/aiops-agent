"""RAG: runbook ranking, past cases, empty results."""
import pytest

from backend import rag, store


@pytest.fixture(autouse=True)
def clean():
    store.reset()
    store.INCIDENTS.clear()
    rag.reload_runbooks()
    yield
    store.reset()
    store.INCIDENTS.clear()


def _incident(service, metric, value, cause="", evidence=None, id="INC-001"):
    return {
        "id": id,
        "service": service,
        "metric": metric,
        "value": value,
        "threshold": 0.20 if metric == "error_rate" else 500,
        "status": "detected",
        "investigation": {"cause": cause, "evidence": evidence or []},
    }


_LATENCY_EVIDENCE = [
    "2026-11-02T00:00:00 [ERROR] inventory: downstream DB query timed out after 850ms",
    "2026-11-02T00:00:00 [WARN] inventory: p95 latency 850ms exceeds 500ms SLO",
]
_ERROR_EVIDENCE = [
    "2026-11-02T00:00:00 [ERROR] payments: request failed with HTTP 500 from payments handler",
    "2026-11-02T00:00:00 [WARN] payments: error rate 35% exceeds 20% SLO",
]


def test_latency_runbook_matches_inventory_latency():
    incident = _incident(
        "inventory", "latency_p95_ms", 850, evidence=_LATENCY_EVIDENCE
    )
    context = rag.get_context(incident)
    assert "Latency spike" in context
    assert "Downstream DB query timeouts" in context
    # Payments is a different service: its runbook must not win here.
    assert "Payments service degradation" not in context


def test_payments_runbook_matches_payments_errors():
    incident = _incident(
        "payments", "error_rate", 0.35, evidence=_ERROR_EVIDENCE
    )
    context = rag.get_context(incident)
    assert "Payments service degradation" in context
    assert "Error-rate spike" in context
    assert "Latency spike" not in context


def test_empty_when_nothing_matches():
    incident = _incident("nope", "unknown_metric", 1)
    assert rag.get_context(incident) == ""
    assert rag.get_context(None) == ""
    assert rag.get_context({}) == ""


def test_past_cases_included():
    past = _incident(
        "inventory", "error_rate", 0.35,
        cause="inventory is failing requests: error rate 35% over the last minute",
        evidence=_ERROR_EVIDENCE, id="INC-001",
    )
    past["status"] = "resolved"
    past["resolution"] = "Service restarted, health check passing."
    store.INCIDENTS.append(past)

    new = _incident(
        "inventory", "error_rate", 0.40, evidence=_ERROR_EVIDENCE, id="INC-002"
    )
    context = rag.get_context(new)
    assert "Past cases" in context
    assert "INC-001 (resolved)" in context
    assert "error rate 35%" in context
    assert "Service restarted, health check passing." in context


def test_no_past_cases_section_when_none():
    incident = _incident(
        "inventory", "error_rate", 0.35, evidence=_ERROR_EVIDENCE
    )
    assert "Past cases" not in rag.get_context(incident)


def test_fault_types_from_metric_and_evidence():
    assert rag._fault_types({"metric": "error_rate"}) == {"errors"}
    assert rag._fault_types({"metric": "latency_p95_ms"}) == {"latency"}
    assert rag._fault_types({"metric": "unknown_metric"}) == set()
    # A latency break fires the error rule first — the evidence lines
    # still say latency, so both fault types count.
    incident = _incident(
        "inventory", "error_rate", 0.35, evidence=_LATENCY_EVIDENCE
    )
    assert rag._fault_types(incident) == {"errors", "latency"}
    assert rag._fault_type(incident) == "latency"
