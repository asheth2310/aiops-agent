"""Simulated services: orders + inventory + payments.

These are fake services, not real order/inventory/payment systems. Each
tick (every 5s, driven by main.py) produces one latency/error sample
and one or two log lines per service, so the detector and investigator
always have real data to work with — nothing is invented by the LLM.

Break types:
- "latency": service goes slow (800ms+) and error-prone (~35%)
- "errors": service stays fast but fails ~35% of requests
"""
import random

from . import store

# Healthy latency range per service (ms); healthy error rate is 0-3% for all.
HEALTHY_LATENCY = {
    "orders": (60, 110),
    "inventory": (60, 110),
    "payments": (80, 140),
}
HEALTHY_ERROR = (0.0, 0.03)
BROKEN_LATENCY = (800, 950)
BROKEN_ERROR = (0.30, 0.45)


def _log(service, level, message):
    line = f"{store.now_iso()} [{level}] {service}: {message}"
    store.LOGS[service].append(line)
    return line


def tick():
    """Advance both services by one 5-second sample. Returns the samples."""
    samples = {}
    for name, svc in store.SERVICES.items():
        if svc["broken"] and svc["break_type"] == "latency":
            latency = random.randint(*BROKEN_LATENCY)
            error_rate = round(random.uniform(*BROKEN_ERROR), 2)
            status = "degraded"
            _log(name, "ERROR", f"downstream DB query timed out after {latency}ms")
            _log(name, "WARN", f"p95 latency {latency}ms exceeds 500ms SLO")
        elif svc["broken"] and svc["break_type"] == "errors":
            latency = random.randint(150, 220)
            error_rate = round(random.uniform(*BROKEN_ERROR), 2)
            status = "degraded"
            _log(name, "ERROR", f"request failed with HTTP 500 from {name} handler")
            _log(name, "WARN", f"error rate {int(error_rate * 100)}% exceeds 20% SLO")
        else:
            latency = random.randint(*HEALTHY_LATENCY[name])
            error_rate = round(random.uniform(*HEALTHY_ERROR), 2)
            status = "healthy"
            _log(name, "INFO", f"request ok in {latency}ms")

        # Real request volume: a degraded service serves fewer requests.
        requests = random.randint(15, 50) if svc["broken"] else random.randint(40, 120)
        svc["total_requests"] += requests
        store.REQUEST_SNAPSHOTS[name].append(
            (store.now_iso(), svc["total_requests"])
        )

        svc["latency_ms"] = latency
        svc["error_rate"] = error_rate
        svc["status"] = status
        svc["ticks"] += 1
        if status == "healthy":
            svc["healthy_ticks"] += 1
        sample = {
            "ts": store.now_iso(),
            "latency_ms": latency,
            "error_rate": error_rate,
            "status": status,
        }
        store.METRICS[name].append(sample)
        samples[name] = sample
    return samples


def break_service(service, break_type="latency"):
    if service not in store.SERVICES:
        raise ValueError(f"unknown service: {service}")
    if break_type not in ("latency", "errors"):
        raise ValueError(f"unknown break type: {break_type}")
    svc = store.SERVICES[service]
    svc["broken"] = True
    svc["break_type"] = break_type
    svc["broken_since"] = store.now_iso()
    _log(service, "WARN", f"simulated fault injected: {break_type}")
    return svc


def recover_service(service):
    if service not in store.SERVICES:
        raise ValueError(f"unknown service: {service}")
    svc = store.SERVICES[service]
    svc["broken"] = False
    svc["break_type"] = None
    svc["broken_since"] = None
    svc["status"] = "healthy"
    svc["latency_ms"] = 82
    svc["error_rate"] = 0.01
    _log(service, "INFO", "service recovered, health check passing")
    return svc


def health():
    return {
        "services": [
            {
                "name": svc["name"],
                "status": svc["status"],
                "latency_ms": svc["latency_ms"],
                "error_rate": svc["error_rate"],
                "broken": svc["broken"],
                "break_type": svc["break_type"],
                "total_requests": svc["total_requests"],
                "requests_per_min": store.requests_per_min(name),
                "uptime_pct": (
                    round(svc["healthy_ticks"] / svc["ticks"] * 100, 1)
                    if svc["ticks"]
                    else 100.0
                ),
            }
            for name, svc in store.SERVICES.items()
        ],
        "generated_at": store.now_iso(),
    }
