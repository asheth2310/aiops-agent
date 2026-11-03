"""Executor — simulated restart / rollback.

On approval it recovers the broken service (simulated restart), waits
10 seconds, then checks health again. Healthy -> resolved, still
degraded -> failed. Failures are recorded, not hidden.
"""
import asyncio

from . import audit, simulator, store


async def execute_fix(incident, approved_by):
    incident["status"] = "executing"
    audit.log(incident["id"], "executor", "fix_started",
              f"Restarting {incident['service']} (approved by {approved_by})")
    simulator.recover_service(incident["service"])
    await asyncio.sleep(10)

    # Fresh sample so the health check reflects the post-fix state.
    simulator.tick()
    svc = store.SERVICES[incident["service"]]
    incident["resolved_at"] = store.now_iso()
    if svc["status"] == "healthy" and svc["latency_ms"] < 200:
        incident["status"] = "resolved"
        incident["resolution"] = "Service restarted, health check passing."
        audit.log(incident["id"], "executor", "fix_succeeded",
                  f"{incident['service']} healthy after restart "
                  f"(latency {svc['latency_ms']}ms)")
    else:
        incident["status"] = "failed"
        incident["resolution"] = (
            f"Restart ran but {incident['service']} is still {svc['status']} "
            f"(latency {svc['latency_ms']}ms)."
        )
        audit.log(incident["id"], "executor", "fix_failed", incident["resolution"])
    return incident
