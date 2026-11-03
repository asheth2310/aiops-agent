"""Governed AIOps Agent — FastAPI app (MVP).

Pipeline: simulator tick (every 5s) -> detector (hard thresholds) ->
investigator (one Gemini call, or mock) -> guardian (code rules) ->
human approve/reject -> executor (simulated restart, health check).

No step after detection runs without a human in the MVP.
Run:  uvicorn backend.main:app --reload --port 8000
"""
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()  # reads .env for GEMINI_LIVE / GEMINI_MODEL, if present.
# No API key is used anywhere: live Gemini goes through the stored
# custom.gemini credential via its helper; default is the mock investigator.

from . import audit, detector, executor, guardian, investigator, simulator, store

app = FastAPI(title="Governed AIOps Agent (MVP)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_sampling = False

# Built frontend (frontend/dist), served by this same process when present,
# so the hosted demo is one URL: dashboard + API on the same origin.
# Local dev without a build keeps the JSON root and the Vite proxy.
_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


class BreakRequest(BaseModel):
    service: str = "inventory"
    type: str = "latency"


class RecoverRequest(BaseModel):
    service: str = "inventory"


class ApproveRequest(BaseModel):
    approved_by: str = "aagam"
    action: str = "restart_service"


class RejectRequest(BaseModel):
    reason: str = ""
    rejected_by: str = "aagam"


async def _investigate(incident):
    """Run investigator + guardian for a new incident, then audit both."""
    incident["status"] = "investigating"
    store.add_timeline(incident, "investigating", "reading logs and metrics")
    logs = list(store.LOGS[incident["service"]])
    metrics = list(store.METRICS[incident["service"]])
    # Investigator is sync (urllib); run it in a thread so the sampler keeps ticking.
    result = await asyncio.to_thread(investigator.investigate, incident, logs, metrics)
    incident["investigation"] = result
    incident["guardian"] = guardian.evaluate(result["confidence"])
    incident["status"] = incident["guardian"]["decision"]
    store.add_timeline(
        incident, "guardian_decision",
        f"{incident['guardian']['decision']} — confidence {result['confidence']}% "
        f"(source: {result['source']})",
    )
    audit.log(
        incident["id"], "investigator", "investigation_done",
        f"source={result['source']} confidence={result['confidence']} cause={result['cause']}",
    )
    audit.log(
        incident["id"], "guardian", incident["guardian"]["decision"],
        incident["guardian"]["reason"],
    )


async def _sampler():
    """Tick the simulator every 5s and run the detector on each service."""
    global _sampling
    _sampling = True
    while _sampling:
        simulator.tick()
        for name in store.SERVICES:
            incident = detector.check(name)
            if incident:
                audit.log(
                    incident["id"], "detector", "incident_detected",
                    f"{name}: {incident['metric']}={incident['value']} "
                    f"(threshold {incident['threshold']})",
                )
                asyncio.create_task(_investigate(incident))
        await asyncio.sleep(5)


@app.on_event("startup")
async def startup():
    simulator.tick()
    asyncio.create_task(_sampler())


@app.get("/")
def root():
    # Hosted mode: serve the built dashboard at / when frontend/dist exists.
    # Local dev (no dist): keep the JSON root.
    index = _DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"service": "Governed AIOps Agent (MVP)", "docs": "/docs"}


@app.get("/health")
def get_health():
    return simulator.health()


@app.get("/logs")
def get_logs(limit: int = 50):
    """Recent simulator log lines across all services, newest first.

    Log lines are stored as plain strings ("ts [LEVEL] service: message")
    so they are parsed back into structured entries here.
    """
    import re

    limit = max(1, min(int(limit), 100))
    pattern = re.compile(r"^(\S+) \[(\w+)\] (\w+): (.*)$")
    entries = []
    for service, lines in store.LOGS.items():
        for line in lines:
            m = pattern.match(line)
            if m:
                entries.append(
                    {
                        "ts": m.group(1),
                        "service": m.group(3),
                        "level": m.group(2),
                        "message": m.group(4),
                    }
                )
            else:
                entries.append(
                    {"ts": "", "service": service, "level": "INFO", "message": line}
                )
    entries.sort(key=lambda e: e["ts"], reverse=True)
    return {"logs": entries[:limit]}


@app.get("/metrics")
def get_metrics(service: str = "inventory", limit: int = 60):
    if service not in store.SERVICES:
        raise HTTPException(status_code=404, detail=f"unknown service: {service}")
    limit = max(1, min(int(limit), 120))
    samples = list(store.METRICS[service])[-limit:]
    return {"service": service, "samples": samples}


@app.post("/demo/reset")
def demo_reset():
    store.reset()
    return {"ok": True, "services": [svc["name"] for svc in store.SERVICES.values()]}


@app.get("/incidents")
def list_incidents():
    return {"incidents": list(reversed(store.INCIDENTS))}


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str):
    for inc in store.INCIDENTS:
        if inc["id"] == incident_id:
            return inc
    raise HTTPException(status_code=404, detail="incident not found")


@app.get("/eval")
def get_eval():
    """Latest evaluation-harness results, if a run has been recorded."""
    results_file = Path(__file__).resolve().parent / "eval" / "results.json"
    if not results_file.exists():
        return {"runs": [], "summary": None, "note": "no eval run yet"}
    return json.loads(results_file.read_text())


@app.get("/audit")
def get_audit():
    return {"audit": audit.all_entries()}


@app.post("/demo/break")
def demo_break(req: BreakRequest):
    try:
        svc = simulator.break_service(req.service, req.type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit.log("—", "demo", "fault_injected", f"{req.service} broken ({req.type})")
    return {"ok": True, "service": svc["name"], "break_type": svc["break_type"]}


@app.post("/demo/recover")
def demo_recover(req: RecoverRequest | None = None):
    service = req.service if req else "inventory"
    try:
        svc = simulator.recover_service(service)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "service": svc["name"], "status": svc["status"]}


@app.post("/incidents/{incident_id}/approve")
async def approve_incident(incident_id: str, req: ApproveRequest):
    incident = next((i for i in store.INCIDENTS if i["id"] == incident_id), None)
    if not incident:
        raise HTTPException(status_code=404, detail="incident not found")
    if incident["status"] == "resolved":
        raise HTTPException(status_code=400, detail="incident already resolved")
    if not incident.get("guardian") or not incident["guardian"]["can_approve"]:
        raise HTTPException(
            status_code=400,
            detail="Guardian has not cleared this incident for approval",
        )
    incident["approved_by"] = req.approved_by
    store.add_timeline(
        incident, "approved",
        f"approved by {req.approved_by} — action: {req.action}",
    )
    audit.log(incident["id"], req.approved_by, "approved", f"action={req.action}")
    # Executor recovers the service, waits 10s, then checks health.
    await executor.execute_fix(incident, req.approved_by)
    return incident


@app.post("/incidents/{incident_id}/reject")
def reject_incident(incident_id: str, req: RejectRequest):
    incident = next((i for i in store.INCIDENTS if i["id"] == incident_id), None)
    if not incident:
        raise HTTPException(status_code=404, detail="incident not found")
    incident["status"] = "rejected"
    incident["resolution"] = req.reason or "Rejected by human reviewer."
    store.add_timeline(incident, "rejected", incident["resolution"])
    audit.log(incident["id"], req.rejected_by, "rejected", incident["resolution"])
    return incident


if (_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")
