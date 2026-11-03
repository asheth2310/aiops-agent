"""Investigator: the direct Gemini REST path (GEMINI_API_KEY).

The REST call is tested with a mocked urlopen, so no real network and
no real key are involved anywhere. The fake key below is a test
fixture string, not a credential.
"""
import io
import json

import pytest

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

METRICS = [
    {"ts": "2026-11-02T00:00:00", "latency_ms": 900, "error_rate": 0.35, "status": "degraded"},
]

FAKE_KEY = "test-key-not-a-real-credential"


def _gemini_response(cause="inventory is failing requests", evidence=None, confidence=91):
    """A fake generateContent JSON body, as the real API returns it."""
    text = json.dumps({
        "cause": cause,
        "evidence": evidence if evidence is not None else LOGS[:3],
        "confidence": confidence,
        "proposed_fix": "Restart the inventory service.",
    })
    return {
        "candidates": [
            {"content": {"parts": [{"text": text}]}}
        ]
    }


class _FakeResponse:
    """Minimal urlopen stand-in: context manager with .read()."""

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def no_helper(monkeypatch):
    """Never touch the real helper binary in these tests."""
    monkeypatch.setattr(investigator, "_call_gemini_helper", lambda *a: None)


@pytest.fixture
def live_env(monkeypatch):
    monkeypatch.setenv("GEMINI_LIVE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)


def test_rest_call_uses_key_header_and_model_url(monkeypatch, no_helper, live_env):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["key_header"] = req.get_header("X-goog-api-key")
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse(_gemini_response())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = investigator.investigate(INCIDENT, LOGS, METRICS)

    assert result["source"] == "gemini"
    assert result["confidence"] == 91
    assert result["evidence"] == LOGS[:3]
    assert result["model"] == "gemini-3.5-flash-lite"
    assert "gemini-3.5-flash-lite" in seen["url"]
    assert seen["url"].endswith(":generateContent")
    assert seen["key_header"] == FAKE_KEY
    # The prompt carries the real metric and log lines.
    prompt = seen["body"]["contents"][0]["parts"][0]["text"]
    assert "inventory" in prompt
    assert LOGS[0] in prompt


def test_rest_falls_back_to_mock_when_api_errors(monkeypatch, no_helper, live_env):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = investigator.investigate(INCIDENT, LOGS, METRICS)

    assert result["source"] == "mock-fallback"
    assert result["confidence"] == 82  # deterministic mock value
    assert len(result["evidence"]) == 3


def test_rest_rejects_hallucinated_evidence_then_falls_back(
    monkeypatch, no_helper, live_env
):
    def fake_urlopen(req, timeout=None):
        return _FakeResponse(
            _gemini_response(evidence=[LOGS[0], LOGS[1], "invented line"])
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = investigator.investigate(INCIDENT, LOGS, METRICS)

    assert result["source"] == "mock-fallback"
    for line in result["evidence"]:
        assert line in LOGS


def test_rest_tries_fallback_model_after_first_fails(
    monkeypatch, no_helper, live_env
):
    import urllib.error

    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        if "gemini-3.5-flash-lite" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 503, "Unavailable", {}, None)
        return _FakeResponse(_gemini_response())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = investigator.investigate(INCIDENT, LOGS, METRICS)

    assert result["source"] == "gemini"
    assert result["model"] == "gemini-3.1-flash-lite"
    assert len(calls) == 2
    assert "gemini-3.5-flash-lite" in calls[0]
    assert "gemini-3.1-flash-lite" in calls[1]


def test_no_key_and_no_helper_falls_back_to_mock(monkeypatch, no_helper):
    monkeypatch.setenv("GEMINI_LIVE", "1")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def fake_urlopen(req, timeout=None):  # pragma: no cover - must not be called
        raise AssertionError("REST path must not run without a key")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = investigator.investigate(INCIDENT, LOGS, METRICS)

    assert result["source"] == "mock-fallback"
    assert len(result["evidence"]) == 3


def test_live_off_ignores_key_and_uses_mock(monkeypatch, no_helper):
    monkeypatch.delenv("GEMINI_LIVE", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)

    def fake_urlopen(req, timeout=None):  # pragma: no cover - must not be called
        raise AssertionError("no live call when GEMINI_LIVE is not 1")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = investigator.investigate(INCIDENT, LOGS, METRICS)

    assert result["source"] == "mock"
    assert result["confidence"] == 82


def test_call_gemini_rest_returns_none_on_bad_shape(monkeypatch):
    def fake_urlopen(req, timeout=None):
        return _FakeResponse({"unexpected": "shape"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert investigator._call_gemini_rest("prompt", "gemini-3.5-flash-lite", FAKE_KEY) is None


def test_call_gemini_rest_returns_none_on_timeout(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert investigator._call_gemini_rest("prompt", "gemini-3.5-flash-lite", FAKE_KEY) is None
