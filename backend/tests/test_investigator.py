"""Investigator: the evidence guard must reject invented log lines."""
from backend import investigator

LOGS = [
    "2026-11-02T00:00:00 [ERROR] inventory: request failed with HTTP 500 from inventory handler",
    "2026-11-02T00:00:05 [WARN] inventory: error rate 35% exceeds 20% SLO",
    "2026-11-02T00:00:10 [ERROR] inventory: downstream DB query timed out after 850ms",
    "2026-11-02T00:00:15 [INFO] inventory: request ok in 90ms",
]

INCIDENT = {
    "service": "inventory",
    "metric": "error_rate",
    "value": 0.35,
    "threshold": 0.20,
}


def test_mock_quotes_only_real_log_lines():
    result = investigator._mock_result(INCIDENT, LOGS)
    assert result["source"] == "mock"
    assert len(result["evidence"]) == 3
    for line in result["evidence"]:
        assert line in LOGS


def test_evidence_guard_rejects_invented_lines():
    invented = [
        LOGS[0],
        LOGS[1],
        "2026-11-02T00:00:15 [ERROR] inventory: database cluster exploded",
    ]
    assert investigator._evidence_is_real(invented, LOGS) is False


def test_evidence_guard_accepts_real_lines():
    # The guard requires exactly 3 lines, all real.
    assert investigator._evidence_is_real(LOGS[:3], LOGS) is True


def test_evidence_guard_requires_exactly_three_lines():
    assert investigator._evidence_is_real(LOGS[:2], LOGS) is False
    assert investigator._evidence_is_real([], LOGS) is False


def test_parse_rejects_hallucinated_evidence():
    text = (
        '{"cause": "db is down", "evidence": ["'
        + LOGS[0] + '", "' + LOGS[1] + '", "invented line"], '
        '"confidence": 90, "proposed_fix": "restart"}'
    )
    assert investigator._parse_result(text, LOGS) is None


def test_parse_accepts_valid_json_with_real_evidence():
    import json

    text = json.dumps({
        "cause": "inventory is failing requests",
        "evidence": LOGS[:3],
        "confidence": 88,
        "proposed_fix": "Restart the inventory service.",
    })
    result = investigator._parse_result(text, LOGS)
    assert result is not None
    assert result["source"] == "gemini"
    assert result["confidence"] == 88


def test_confidence_breakdown_comes_from_real_data():
    result = investigator._mock_result(INCIDENT, LOGS)
    breakdown = investigator._confidence_breakdown(INCIDENT, result, LOGS)
    assert breakdown["evidence_match"] == "3/3"
    assert breakdown["metric_severity_x"] == 1.7  # round(0.35 / 0.20, 1)
    assert breakdown["log_signal_n"] == 3  # two ERROR + one WARN
