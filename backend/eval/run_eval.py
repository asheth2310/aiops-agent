"""Evaluation harness for the Governed AIOps Agent.

Starts the app on a test port, runs a healthy baseline (false-positive
check), then 6 fault runs (3 services x latency/errors): break -> wait
for detection -> check cause + evidence -> approve -> check resolution.

Run with the project's venv python from the repo root:
    .venv/bin/python backend/eval/run_eval.py

Writes backend/eval/results.json and prints a readable table.
Uses only the standard library (urllib). The app runs with the default
mock investigator (GEMINI_LIVE=0), so results are deterministic and
repeatable — this measures the detection/guardian/executor pipeline,
not live-LLM variance. To eval the live investigator instead, set
GEMINI_LIVE=1 in the environment before running.
"""
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVAL_DIR = os.path.join(ROOT, "backend", "eval")
RESULTS_PATH = os.path.join(EVAL_DIR, "results.json")
PORT = int(os.environ.get("EVAL_PORT", "8010"))
BASE = f"http://127.0.0.1:{PORT}"

# For a latency break the error rule fires first (a latency break also
# raises the error rate), so both metrics count as a correct detection.
EXPECTED_METRIC = {
    "latency": {"error_rate", "latency_p95_ms"},
    "errors": {"error_rate"},
}


def _req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _wait_healthy(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _req("GET", "/health")
            return True
        except Exception:
            time.sleep(1)
    return False


def _cause_correct(incident, service, break_type):
    """Service named in the cause, and the fired metric fits the fault."""
    inv = incident.get("investigation") or {}
    cause = (inv.get("cause") or "").lower()
    return (
        service in cause
        and incident.get("metric") in EXPECTED_METRIC[break_type]
    )


def _evidence_valid(incident):
    """Every quoted evidence line appears in the live log stream."""
    inv = incident.get("investigation") or {}
    evidence = inv.get("evidence") or []
    if len(evidence) != 3:
        return False
    try:
        logs = _req("GET", "/logs?limit=100").get("logs", [])
    except Exception:
        return False
    full_lines = [
        f"{e.get('ts','')} [{e.get('level','')}] {e.get('service','')}: {e.get('message','')}"
        for e in logs
    ]
    return all(
        any(line.strip() in full or full.strip() == line.strip() for full in full_lines)
        for line in evidence
    )


def _run_fault(service, break_type):
    """One fault run. Returns a result row dict."""
    row = {
        "service": service,
        "break_type": break_type,
        "detected": False,
        "detect_seconds": None,
        "metric": None,
        "cause_correct": False,
        "confidence": None,
        "source": None,
        "evidence_valid": False,
        "guardian": None,
        "resolve_seconds": None,
        "final_status": None,
    }
    t0 = time.time()
    _req("POST", "/demo/break", {"service": service, "type": break_type})
    incident = None
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            incidents = _req("GET", "/incidents").get("incidents", [])
        except Exception:
            incidents = []
        open_ones = [
            i for i in incidents
            if i["service"] == service
            and i.get("status") in ("needs_approval", "needs_info")
        ]
        if open_ones:
            incident = open_ones[0]
            break
        time.sleep(2)
    if incident is None:
        row["final_status"] = "not_detected"
        return row

    row["detected"] = True
    row["detect_seconds"] = round(time.time() - t0, 1)
    row["metric"] = incident.get("metric")
    row["cause_correct"] = _cause_correct(incident, service, break_type)
    inv = incident.get("investigation") or {}
    row["confidence"] = inv.get("confidence")
    row["source"] = inv.get("source")
    row["evidence_valid"] = _evidence_valid(incident)
    row["guardian"] = (incident.get("guardian") or {}).get("decision")

    t_approve = time.time()
    _req("POST", f"/incidents/{incident['id']}/approve",
         {"approved_by": "eval", "action": "restart_service"})
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            cur = _req("GET", f"/incidents/{incident['id']}")
        except Exception:
            cur = {}
        if cur.get("status") in ("resolved", "failed"):
            row["final_status"] = cur["status"]
            row["resolve_seconds"] = round(time.time() - t_approve, 1)
            break
        time.sleep(1)
    else:
        row["final_status"] = "approve_timeout"
    return row


def main():
    env = dict(os.environ)
    env.setdefault("GEMINI_LIVE", "0")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--port", str(PORT)],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_healthy():
            print("ERROR: app did not become healthy on port", PORT)
            return 1
        _req("POST", "/demo/reset", {})

        # Healthy baseline: 30s with no faults — any incident is a false positive.
        time.sleep(30)
        baseline = _req("GET", "/incidents").get("incidents", [])
        false_positives = len(baseline)

        runs = []
        for service in ("inventory", "orders", "payments"):
            for break_type in ("latency", "errors"):
                row = _run_fault(service, break_type)
                runs.append(row)
                print(f"  {service}/{break_type}: detected={row['detected']} "
                      f"detect={row['detect_seconds']}s cause_ok={row['cause_correct']} "
                      f"evidence_ok={row['evidence_valid']} conf={row['confidence']} "
                      f"guardian={row['guardian']} resolve={row['resolve_seconds']}s "
                      f"final={row['final_status']}")
                _req("POST", "/demo/reset", {})
                time.sleep(2)

        detected = [r for r in runs if r["detected"]]
        detect_times = [r["detect_seconds"] for r in detected if r["detect_seconds"] is not None]
        resolve_times = [r["resolve_seconds"] for r in runs if r["resolve_seconds"] is not None]
        summary = {
            "runs": len(runs),
            "detected": len(detected),
            "cause_correct": sum(1 for r in runs if r["cause_correct"]),
            "evidence_valid": sum(1 for r in runs if r["evidence_valid"]),
            "avg_detect_seconds": round(statistics.mean(detect_times), 1) if detect_times else None,
            "median_detect_seconds": round(statistics.median(detect_times), 1) if detect_times else None,
            "avg_resolve_seconds": round(statistics.mean(resolve_times), 1) if resolve_times else None,
            "false_positives": false_positives,
            "investigator_source": runs[0]["source"] if runs else None,
        }
        results = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "baseline_seconds": 30,
            "runs": runs,
            "summary": summary,
        }
        os.makedirs(EVAL_DIR, exist_ok=True)
        with open(RESULTS_PATH, "w") as f:
            json.dump(results, f, indent=2)

        print()
        print("Evaluation summary")
        print(f"  runs:               {summary['runs']}")
        print(f"  detected:           {summary['detected']}/{summary['runs']}")
        print(f"  cause correct:      {summary['cause_correct']}/{summary['runs']}")
        print(f"  evidence valid:     {summary['evidence_valid']}/{summary['runs']}")
        print(f"  avg detect:         {summary['avg_detect_seconds']}s "
              f"(median {summary['median_detect_seconds']}s)")
        print(f"  avg resolve:        {summary['avg_resolve_seconds']}s")
        print(f"  false positives:    {summary['false_positives']} (30s healthy baseline)")
        print(f"  investigator:       {summary['investigator_source']}")
        print(f"  results: {RESULTS_PATH}")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
