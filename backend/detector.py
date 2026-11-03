"""Detector — hard thresholds only. No LLM is involved in detection.

Rules (from the MVP plan):
- error rate > 20% over the last 1 minute, OR
- latency p95 > 500ms for 2 minutes in a row (two consecutive 1-min windows)

Samples arrive every 5s, so 1 minute = 12 samples, 2 minutes = 24.
A latency break also raises the error rate, so in practice the error
rule fires first and detection lands well under 60 seconds.
"""
from . import store

ERROR_THRESHOLD = 0.20
LATENCY_THRESHOLD_MS = 500
SAMPLES_PER_MINUTE = 12


def _p95(values):
    if not values:
        return 0
    ordered = sorted(values)
    idx = max(0, int(len(ordered) * 0.95) - 1)
    return ordered[idx]


def check(service_name):
    """Check one service. Returns a new incident dict, or None."""
    if store.open_incident(service_name):
        return None  # already an open incident for this service

    samples = list(store.METRICS[service_name])
    if len(samples) < SAMPLES_PER_MINUTE:
        return None
    # The service must be degraded right now. Without this, a resolved
    # incident's stale degraded samples keep the 1-min average above
    # threshold and the detector refires a phantom incident after recovery.
    if samples[-1]["status"] != "degraded":
        return None

    recent = samples[-SAMPLES_PER_MINUTE:]
    avg_error = sum(s["error_rate"] for s in recent) / len(recent)
    if avg_error > ERROR_THRESHOLD:
        return _make_incident(
            service_name, "error_rate", round(avg_error, 2), ERROR_THRESHOLD,
            window_samples=SAMPLES_PER_MINUTE,
            window_label="last 12 samples (1 min)",
            breach_seconds=_trailing_degraded_seconds(samples),
        )

    if len(samples) >= 2 * SAMPLES_PER_MINUTE:
        window1 = samples[-2 * SAMPLES_PER_MINUTE : -SAMPLES_PER_MINUTE]
        window2 = samples[-SAMPLES_PER_MINUTE:]
        p95_1 = _p95([s["latency_ms"] for s in window1])
        p95_2 = _p95([s["latency_ms"] for s in window2])
        if p95_1 > LATENCY_THRESHOLD_MS and p95_2 > LATENCY_THRESHOLD_MS:
            return _make_incident(
                service_name, "latency_p95_ms", max(p95_1, p95_2), LATENCY_THRESHOLD_MS,
                window_samples=2 * SAMPLES_PER_MINUTE,
                window_label="two 1-min windows (24 samples, 2 min)",
                breach_seconds=_trailing_degraded_seconds(samples),
            )
    return None


def _trailing_degraded_seconds(samples):
    """How long the service has been continuously degraded, in seconds.

    Counts trailing degraded samples (5s each) back from the newest one.
    This is the breach duration before detection — the detector only
    fires once a full window is breached, so it is >= 60s for the error
    rule and >= 120s for the latency rule.
    """
    count = 0
    for s in reversed(samples):
        if s["status"] == "degraded":
            count += 1
        else:
            break
    return count * 5


def _make_incident(service_name, metric, value, threshold, window_samples,
                   window_label, breach_seconds):
    incident = {
        "id": store.next_incident_id(),
        "service": service_name,
        "metric": metric,
        "value": value,
        "threshold": threshold,
        "status": "detected",
        "started_at": store.now_iso(),
        "detected_at": store.now_iso(),
        "investigation": None,
        "guardian": None,
        "approved_by": None,
        "resolved_at": None,
        "resolution": None,
        "detection": {
            "metric": metric,
            "value": value,
            "threshold": threshold,
            "window_samples": window_samples,
            "window_label": window_label,
            "breach_seconds": breach_seconds,
        },
        "timeline": [],
    }
    store.add_timeline(
        incident, "detected",
        f"{metric} {value} vs threshold {threshold} "
        f"(breach lasted {breach_seconds}s before detection)",
    )
    store.INCIDENTS.append(incident)
    return incident
